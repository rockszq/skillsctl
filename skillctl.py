#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 rockszq
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""skillctl — 跨工具 SKILL 单源管理（flavor 模型 + Windows 目录联接）。

单源仓保存唯一的 skill 内容；各工具目录里的 skill 是指向仓内 flavor 的 junction。
flavor 允许同名 skill 保存多个内容版本，manifest 记录“哪个工具用哪个版本”，
因此集中化不会强迫立刻统一内容。

用法：
  python skillctl.py import              扫描三地建 flavor 与 manifest（不改原目录）
  python skillctl.py add NAME --from DIR 在仓内新增 skill（默认三工具全部链接）
  python skillctl.py add NAME --empty    生成骨架 SKILL.md 再自行填写
  python skillctl.py exclude NAME --tool T --reason R
                                         登记「已知但不纳入」的 skill（市场安装／外部提供），verify 不再告警
  python skillctl.py refresh             编辑仓内内容后刷新 manifest 哈希
  python skillctl.py link [--only NAME] [--dry-run]
                                         备份原目录并替换为 junction
  python skillctl.py verify              校验链接指向与内容哈希（非 0 表示有问题）
  python skillctl.py status              查看 flavor 分布与可统一候选
  python skillctl.py unify NAME --to F   把一个 skill 的所有工具指向同一 flavor
  python skillctl.py prune [--apply]     清理无工具指向的孤儿 flavor（默认只列）
  python skillctl.py rollback [TS]       按备份时间点还原
  python skillctl.py check-secrets       扫描仓内硬编码密钥
  python skillctl.py normalize-eol       把仓内 SKILL.md 行尾统一为 LF（内容不变）
  python skillctl.py drift [--min-ratio R]  列出正文相似度过低的多版本 flavor
  python skillctl.py selftest            临时目录自检，不触碰真实目录
"""
import argparse
import difflib
from functools import lru_cache
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
REPO = os.environ.get("SKILLSCTL_REPO") or os.path.join(HOME, ".skills-src")
DEFAULT_TOOLS = {
    "workbuddy": os.path.join(HOME, ".workbuddy", "skills"),
    "qoder": os.path.join(HOME, ".qoder", "skills"),
    "opencode": os.path.join(HOME, ".config", "opencode", "skills"),
    "dsh": os.path.join(HOME, ".dsh", "skills"),
}


def load_tools(repo):
    """tools.json: {"tool-name": "~/path/to/skills"}; a missing file keeps defaults."""
    path = os.path.join(repo, "tools.json")
    if not os.path.isfile(path):
        return dict(DEFAULT_TOOLS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return dict(DEFAULT_TOOLS)
    if not isinstance(data, dict):
        return dict(DEFAULT_TOOLS)
    tools = {k: os.path.expanduser(v) for k, v in data.items()
             if isinstance(k, str) and isinstance(v, str)}
    return tools or dict(DEFAULT_TOOLS)


CTX = {
    "repo": REPO,
    "skills": os.path.join(REPO, "skills"),
    "manifest": os.path.join(REPO, "manifest.json"),
    "backup": os.path.join(HOME, ".skills-backup"),
    "tools": load_tools(REPO),
}
TOOL_ORDER = tuple(CTX["tools"].keys())
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", ".codegraph"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
RUNTIME_DIRS = {"history", "reviews"}
EXCLUDE_PREFIX = ("_", "backup")
IO_REPARSE_LINK = (0xA0000003, 0xA000000C)  # junction / symlink


# ---------------------------------------------------------------- 基础工具

def log(msg=""):
    print(msg, flush=True)


def norm(p):
    return os.path.normcase(os.path.realpath(p))


def same_path(a, b):
    return norm(a) == norm(b)


def is_linklike(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if os.name == "nt":
        return getattr(st, "st_reparse_tag", 0) in IO_REPARSE_LINK
    return stat.S_ISLNK(st.st_mode)


def is_under(path, root):
    p, r = norm(path), norm(root)
    return p == r or p.startswith(r + os.sep)


def _is_junk(name, is_dir):
    """陈旧备份与归档不属于 skill 内容：不复制、不参与哈希。"""
    low = name.lower()
    if low in SKIP_FILES:
        return True
    if low.startswith(".bak") or ".bak" in low:
        return True
    if is_dir and (low.startswith("_backup") or low.startswith("_archive")
                   or low.startswith("scripts_bak_")):
        return True
    return False


@lru_cache(maxsize=None)
def _tracked_rels(repo, flavor):
    """git 会分发的相对路径集合；非 git 仓返回 None（selftest 用全目录口径）。"""
    prefix = "skills/" + flavor + "/"
    r = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotepath=false",
         "ls-files", "-z", "--", "skills/" + flavor],
        capture_output=True)
    if r.returncode != 0:
        return None
    return {p[len(prefix):] for p in r.stdout.decode("utf-8").split("\0")
            if p.startswith(prefix)}


def tracked_rels(flavor):
    return _tracked_rels(CTX["repo"], flavor)


def tree_hash(path, runtime=False):
    """对目录内容求 sha256。口径 = git 会分发的文件；本机被 ignore 的
    运行期数据不进内容身份，否则干净克隆后 verify 必报 hash-mismatch。"""
    h = hashlib.sha256()
    base = os.path.abspath(path)
    real = os.path.realpath(base)
    allowed = None
    if is_under(real, CTX["skills"]):
        flavor = os.path.relpath(real, CTX["skills"]).split(os.sep)[0]
        if flavor != ".":
            allowed = tracked_rels(flavor)
    for dp, dn, fn in os.walk(base):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS
                       and not _is_junk(d, True)
                       and (runtime or d not in RUNTIME_DIRS))
        for f in sorted(fn):
            if f in SKIP_FILES or _is_junk(f, False):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            if allowed is not None and rel not in allowed:
                continue
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            try:
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            except OSError:
                h.update(b"<unreadable>")
            h.update(b"\0")
    return h.hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_files(path):
    n = 0
    for dp, dn, fn in os.walk(path):
        dn[:] = [d for d in dn if d not in SKIP_DIRS
                 and not _is_junk(d, True) and d not in RUNTIME_DIRS]
        n += sum(1 for f in fn if f not in SKIP_FILES and not _is_junk(f, False))
    return n


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REF_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|sh|js|json|md|ya?ml|txt))`")
REF_DIRS = ("scripts/", "references/", "assets/", "templates/", "knowledge/")


def missing_refs(flavor, text):
    """SKILL.md 里点名的随附文件是否存在；缺失会在调用时才发现，故前置暴露。"""
    out = []
    for rel in REF_RE.findall(text):
        if rel.startswith("/") or ".." in rel or not rel.startswith(REF_DIRS):
            continue
        if not os.path.exists(os.path.join(CTX["skills"], flavor, rel)) and rel not in out:
            out.append(rel)
    return out


def external_refs(flavor):
    """references/SCRIPT_INVENTORY.md 登记过、由项目侧提供的文件。

    这类引用本就不该存在于 skill 目录（v7 脚本在项目根 scripts/ 下），
    所以 verify 只记一行 ext-ref 信息，不报 missing-ref；没登记的仍告警。
    """
    p = os.path.join(CTX["skills"], flavor, "references", "SCRIPT_INVENTORY.md")
    if not os.path.exists(p):
        return set()
    with open(p, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    ack = set()
    for tok in REF_RE.findall(text):
        if tok.startswith("scripts/"):
            ack.add(tok)
        elif "/" not in tok and tok.endswith(".py"):
            ack.add("scripts/" + tok)
    return ack


def read_frontmatter(path):
    """只取 YAML frontmatter 的顶层标量键（含块标量续行）；解析失败返回 None。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    if not text.lstrip("\ufeff").startswith("---"):
        return None
    text = text.lstrip("\ufeff")
    end = text.find("\n---", 3)
    if end < 0:
        return None
    out, key = {}, None
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t" and ":" in line:
            key, val = line.split(":", 1)
            out[key.strip()] = val.strip()
        elif key and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def _write_lf(p, text):
    """以 LF 行尾写文件，统一仓内行尾（git 不做换行转换，混用会让 diff 整篇标改）。"""
    with open(p, "wb") as f:
        f.write(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _skill_body(path):
    """读 SKILL.md，剥掉 frontmatter，返回正文行。"""
    text = open(path, encoding="utf-8", errors="replace").read()
    return _split_fm(text)[1].splitlines()


def flavor_similarity(base, other):
    """两个 flavor 的 SKILL.md 相似度（0-1），以首个 flavor 为基准。"""
    a = "\n".join(_skill_body(os.path.join(CTX["skills"], base, "SKILL.md")))
    b = "\n".join(_skill_body(os.path.join(CTX["skills"], other, "SKILL.md")))
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def check_eol(flavor, path):
    """返回 (crlf, lf) 计数；混用或 CRLF 都算问题。"""
    raw = open(path, "rb").read()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return crlf, lf


def cmd_normalize_eol(args):
    """把仓内 SKILL.md 统一为 LF 行尾（内容不变，只改行尾），随后需 refresh。"""
    m = load_manifest()
    fixed = []
    for name, ent in sorted(m["skills"].items()):
        for flavor in sorted(ent["flavors"]):
            p = os.path.join(CTX["skills"], flavor, "SKILL.md")
            if not os.path.exists(p):
                continue
            crlf, lf = check_eol(flavor, p)
            if crlf == 0:
                continue
            text = open(p, encoding="utf-8", errors="replace").read()
            _write_lf(p, text)
            fixed.append((flavor, crlf, lf))
    for flavor, crlf, lf in fixed:
        log(f"  ~ {flavor}: CRLF {crlf} -> LF（原 LF {lf} 行保留）")
    log(f"normalize-eol: {len(fixed)} 个文件改为 LF；改后请跑 refresh 更新哈希")
    return 0


def cmd_drift(args):
    """列出多版本 skill 中正文相似度过低的 flavor（低于 --min 视为需分诊）。

    同时给出差异位置：仅 frontmatter 不同说明内容同源、可直接统一；
    正文不同则多为工具专属语法（如 OpenCode 的 superpowers:/@ 引用），统一会破坏它。
    """
    m = load_manifest()
    rows = []
    for name, ent in sorted(m["skills"].items()):
        flavors = sorted(ent["flavors"])
        if len(flavors) < 2:
            continue
        base = flavors[0]
        base_fm, base_body = _split_fm("\n".join(_read_skill_md(base)))
        for f in flavors[1:]:
            ratio = flavor_similarity(base, f)
            if ratio < args.min_ratio:
                fm, body = _split_fm("\n".join(_read_skill_md(f)))
                where = _where_differs(base_fm, base_body, fm, body)
                rows.append((name, base, f, ratio, where))
    for name, base, f, ratio, where in rows:
        log(f"  {name}: {base} vs {f} = {ratio:.1%} ({where})")
    log(f"drift: {len(rows)} 个 flavor 相似度低于 {args.min_ratio:.1%}（共 "
        f"{sum(len(v['flavors']) for v in m['skills'].values())} 个 flavor）")
    return 0


def copy_skill(src, dst):
    def ignore(d, names):
        return [n for n in names
                if n in SKIP_DIRS or _is_junk(n, os.path.isdir(os.path.join(d, n)))]
    shutil.copytree(src, dst, ignore=ignore)


def merge_runtime(src, dst, tool):
    """运行期数据不参与身份判定，但迁移时并集保存，避免丢历史。"""
    for rd in RUNTIME_DIRS:
        s = os.path.join(src, rd)
        if not os.path.isdir(s):
            continue
        for dp, dn, fn in os.walk(s):
            rel = os.path.relpath(dp, s)
            d2 = os.path.join(dst, rd, rel)
            os.makedirs(d2, exist_ok=True)
            for f in fn:
                sp, tp = os.path.join(dp, f), os.path.join(d2, f)
                if not os.path.exists(tp):
                    shutil.copy2(sp, tp)
                elif file_hash(sp) != file_hash(tp):
                    alt = tp + f".from-{tool}"
                    if not os.path.exists(alt):
                        shutil.copy2(sp, alt)
                        log(f"  runtime conflict {rd}/{os.path.relpath(sp, s)} -> {os.path.basename(alt)}")


def make_junction(link, target):
    if os.path.lexists(link):
        raise OSError(f"link path exists: {link}")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    r = subprocess.run(
        f'cmd /c mklink /J "{link}" "{target}"',
        shell=True, capture_output=True,
    )
    if r.returncode != 0 or not os.path.isdir(link):
        out = (r.stdout + r.stderr).decode("mbcs", errors="replace").strip()
        raise OSError(f"mklink failed for {link}: {out}")


def remove_junction(path):
    """只删除链接本身；调用方必须先确认它是链接且目标在仓内。"""
    if not is_linklike(path):
        raise OSError(f"refusing to remove non-link: {path}")
    os.rmdir(path)


# ---------------------------------------------------------------- manifest

def load_manifest(required=True):
    if not os.path.exists(CTX["manifest"]):
        if required:
            raise SystemExit("manifest 不存在，先运行 import")
        return None
    with open(CTX["manifest"], encoding="utf-8") as f:
        return json.load(f)


def save_manifest(m):
    os.makedirs(os.path.dirname(CTX["manifest"]), exist_ok=True)
    m["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(CTX["manifest"], "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------- add

SKILL_TEMPLATE = """---
name: {name}
description: TODO 一句话说明「什么时候该用」，包含触发词。
---

# {name}

## 何时使用

- TODO

## 做法

1. TODO
"""


def cmd_add(args):
    m = load_manifest()
    name = args.name
    if name in m["skills"]:
        raise SystemExit(f"already managed: {name}（改内容请直接编辑仓内目录后 refresh）")
    if not NAME_RE.match(name):
        raise SystemExit("skill 名只能是小写字母/数字/连字符")
    flavor = args.flavor or name
    dst = os.path.join(CTX["skills"], flavor)
    if os.path.isdir(dst):
        raise SystemExit(f"flavor 目录已存在: {dst}")
    tools = [t.strip() for t in (args.tools or ",".join(TOOL_ORDER)).split(",") if t.strip()]
    unknown = [t for t in tools if t not in TOOL_ORDER]
    if unknown:
        raise SystemExit(f"unknown tool(s): {unknown}; choose from {list(TOOL_ORDER)}")

    if args.empty:
        os.makedirs(dst)
        _write(os.path.join(dst, "SKILL.md"), SKILL_TEMPLATE.format(name=name))
    else:
        if not args.source:
            raise SystemExit("需要 --from DIR 或 --empty")
        src = os.path.abspath(args.source)
        if not os.path.exists(os.path.join(src, "SKILL.md")):
            raise SystemExit(f"源目录没有 SKILL.md: {src}")
        copy_skill(src, dst)

    fm = read_frontmatter(os.path.join(dst, "SKILL.md"))
    if not fm or fm.get("name") != name:
        shutil.rmtree(dst, ignore_errors=True)
        got = fm.get("name") if fm else None
        raise SystemExit(f"frontmatter name 必须等于 {name!r}，当前 {got!r}（已回滚新目录）")
    if not fm.get("description"):
        shutil.rmtree(dst, ignore_errors=True)
        raise SystemExit("frontmatter 缺少 description（已回滚新目录）")

    m["skills"][name] = {
        "flavors": {flavor: {"hash": tree_hash(dst), "files": count_files(dst)}},
        "targets": {t: flavor for t in tools},
    }
    save_manifest(m)
    log(f"add: {name} -> skills/{flavor}，登记工具 {','.join(tools)}")
    if args.no_link:
        log(f"next: python skillctl.py link --only {name}")
        return 0
    return cmd_link(argparse.Namespace(only=name, dry_run=False, ts=None))


# ---------------------------------------------------------------- import

def scan_tool(tool):
    root = CTX["tools"][tool]
    out = {}
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        out[name] = {
            "path": p,
            "skill": os.path.exists(os.path.join(p, "SKILL.md")),
            "link": is_linklike(p),
            "real": os.path.realpath(p),
        }
    return out


def cmd_import(args):
    m = {"schema": 1, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "skills": {}, "excluded": []}
    live = {t: scan_tool(t) for t in TOOL_ORDER}
    names = sorted(set().union(*[set(v) for v in live.values()]))
    fresh = 0
    for name in names:
        found = []
        for tool in TOOL_ORDER:
            rec = live[tool].get(name)
            if not rec:
                continue
            if name.startswith(EXCLUDE_PREFIX):
                m["excluded"].append({"tool": tool, "name": name, "reason": "backup-or-non-skill"})
                continue
            if rec["link"]:
                if is_under(rec["real"], CTX["skills"]):
                    # 已由本仓托管：按链接实际内容重建条目，保证重复 import 幂等
                    found.append((tool, tree_hash(rec["real"]), rec["real"]))
                    continue
                m["excluded"].append({"tool": tool, "name": name, "reason": "external-link"})
                continue
            if not rec["skill"]:
                m["excluded"].append({"tool": tool, "name": name, "reason": "no-SKILL.md"})
                continue
            found.append((tool, tree_hash(rec["path"]), rec["path"]))
        if not found:
            continue

        by_hash = {}
        for tool, h, src in found:
            by_hash.setdefault(h, []).append((tool, src))
        canonical_hash = found[0][1]  # TOOL_ORDER 优先
        flavors, targets = {}, {}
        for h, members in by_hash.items():
            if h == canonical_hash:
                flavor = name
            else:
                flavor = f"{name}__{members[0][0]}"
            dst = os.path.join(CTX["skills"], flavor)
            if not os.path.isdir(dst):
                copy_skill(members[0][1], dst)
                fresh += 1
            for tool, src in members:
                if os.path.isdir(src):
                    merge_runtime(src, dst, tool)
            copied = tree_hash(dst)
            if copied != h:
                raise SystemExit(f"copy mismatch for {flavor}: {copied} != {h}")
            flavors[flavor] = {"hash": h, "files": count_files(dst)}
            for tool, _ in members:
                targets[tool] = flavor
        m["skills"][name] = {"flavors": flavors, "targets": targets}

    save_manifest(m)
    log(f"import: {len(m['skills'])} skills, {sum(len(v['flavors']) for v in m['skills'].values())} flavors, "
        f"{fresh} new dirs, {len(m['excluded'])} excluded")
    for e in m["excluded"]:
        log(f"  - {e['tool']}/{e['name']}: {e['reason']}")


def cmd_exclude(args):
    """登记「已知但不纳入本仓」的 skill：平台市场安装、由外部提供的能力等。

    不登记则 verify 每次都会把它报成 unmanaged——那是提示，不是门禁；
    登记后 verify 才反映「已决策」而非「被遗漏」。
    """
    if args.tool not in TOOL_ORDER:
        raise SystemExit(f"unknown tool: {args.tool}; choose from {list(TOOL_ORDER)}")
    if not NAME_RE.match(args.name):
        raise SystemExit("skill 名只能是小写字母/数字/连字符")
    m = load_manifest()
    if args.name in m["skills"]:
        raise SystemExit(
            f"already managed: {args.name}（它已纳入本仓，exclude 只用于不纳入的目录）")
    order = {t: i for i, t in enumerate(TOOL_ORDER)}
    entries = {(e["tool"], e["name"]): e for e in m.get("excluded", [])}
    key = (args.tool, args.name)
    if key in entries:
        raise SystemExit(f"already excluded: {args.tool}/{args.name}")
    rec = scan_tool(args.tool).get(args.name)
    if rec is None:
        raise SystemExit(
            f"not found: {args.tool}/{args.name}（exclude 只用于真实存在的目录；"
            f"确为市场安装／外部提供时，请先确认目录名拼写）")
    entries[key] = {"tool": args.tool, "name": args.name, "reason": args.reason}
    m["excluded"] = [entries[k] for k in sorted(entries, key=lambda k: (order[k[0]], k[1]))]
    save_manifest(m)
    log(f"exclude: {args.tool}/{args.name} ({args.reason})")


def cmd_refresh(args):
    m = load_manifest()
    changed = 0
    for name, ent in m["skills"].items():
        for flavor in list(ent["flavors"]):
            d = os.path.join(CTX["skills"], flavor)
            h = tree_hash(d)
            if h != ent["flavors"][flavor]["hash"]:
                log(f"  ~ {flavor}: {ent['flavors'][flavor]['hash'][:8]} -> {h[:8]}")
                ent["flavors"][flavor] = {"hash": h, "files": count_files(d)}
                changed += 1
    save_manifest(m)
    log(f"refresh: {changed} flavor(s) updated")


# ---------------------------------------------------------------- link

def _link_one(ts, tool, name, ent, flavor, dry_run):
    root = CTX["tools"][tool]
    link = os.path.join(root, name)
    target = os.path.join(CTX["skills"], flavor)
    if not os.path.isdir(target):
        raise RuntimeError(f"missing flavor dir: {target}")
    if not os.path.isdir(link):
        if dry_run:
            return "plan"
        make_junction(link, target)
        if not same_path(os.path.realpath(link), target):
            raise RuntimeError(f"link verification failed: {link}")
        return "linked"
    if is_linklike(link):
        if same_path(os.path.realpath(link), target):
            return "ok"
        if not is_under(os.path.realpath(link), CTX["skills"]):
            raise RuntimeError(f"refusing to replace external link: {link}")
        if dry_run:
            return "plan"
        remove_junction(link)
        make_junction(link, target)
        if not same_path(os.path.realpath(link), target):
            raise RuntimeError(f"link verification failed: {link}")
        return "relinked"
    want = ent["flavors"][flavor]["hash"]
    live = tree_hash(link)
    if live != want:
        raise RuntimeError(f"content differs from manifest ({live[:8]} != {want[:8]}); "
                           f"review then rerun import")
    if dry_run:
        return "plan"
    bak = os.path.join(CTX["backup"], ts, tool, name)
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    if os.path.exists(bak):
        raise RuntimeError(f"backup path exists: {bak}")
    shutil.move(link, bak)
    try:
        if tree_hash(bak) != want:
            raise RuntimeError("backup verification failed")
        make_junction(link, target)
        if not same_path(os.path.realpath(link), target):
            raise RuntimeError(f"link verification failed: {link}")
    except Exception:
        if not os.path.lexists(link):
            shutil.move(bak, link)
        raise
    return "linked"


def cmd_link(args):
    m = load_manifest()
    ts = args.ts or time.strftime("%Y%m%d-%H%M%S")
    stats = {"linked": 0, "relinked": 0, "ok": 0, "plan": 0, "fail": 0}
    for name, ent in sorted(m["skills"].items()):
        if args.only and name != args.only:
            continue
        for tool, flavor in sorted(ent["targets"].items()):
            if tool == "dsh" and not NAME_RE.match(name):
                # DSH 只认 kebab-case 的 frontmatter name，非法名被它静默跳过——
                # 部署时刻硬拒比部署后 verify 告警更早暴露（cmd_add 拦 add，这里拦部署）。
                stats["fail"] += 1
                log(f"  FAIL dsh/{name}: name={name!r} 非 kebab-case，DSH 会静默忽略"
                    "（改名后再链，或去掉 dsh 目标）")
                continue
            try:
                r = _link_one(ts, tool, name, ent, flavor, args.dry_run)
            except Exception as e:
                stats["fail"] += 1
                log(f"  FAIL {tool}/{name}: {e}")
                continue
            stats[r] += 1
    log(f"link: {stats['linked']} linked, {stats['relinked']} relinked, "
        f"{stats['ok']} already-ok, {stats['plan']} planned (dry-run={args.dry_run}), "
        f"{stats['fail']} failed, backup ts={ts}")
    return 1 if stats["fail"] else 0


# ---------------------------------------------------------------- verify

def cmd_verify(args):
    m = load_manifest()
    errors, warnings, warns, extrefs = [], [], [], []
    seen, checked = set(), set()
    for name, ent in sorted(m["skills"].items()):
        for flavor in sorted(ent["flavors"]):
            # 内容层自检：缺 SKILL.md / frontmatter 不合法会让工具静默丢载这个 skill
            if flavor in checked:
                continue
            checked.add(flavor)
            sk = os.path.join(CTX["skills"], flavor, "SKILL.md")
            if not os.path.exists(sk):
                errors.append(f"no-SKILL.md: {flavor}")
                continue
            fm = read_frontmatter(sk)
            if fm is None:
                errors.append(f"bad-frontmatter: {flavor}")
                continue
            crlf, lf = check_eol(flavor, sk)
            if crlf:
                warnings.append(f"crlf-eol: {flavor} ({crlf} CRLF / {lf} LF)")
            if fm.get("name") != name:
                errors.append(f"name-mismatch: {flavor} (name={fm.get('name')!r}, dir={name!r})")
            elif not NAME_RE.match(name) and ent["targets"].get("dsh") == flavor:
                # DSH 的 SKILL_NAME 正则与本条一致；不合法它只打日志静默跳过，
                # 其余三工具却宽容下划线——最容易漏的就是这种「一家不加载」。
                # 只对 targets.dsh 指向的 flavor 告警：DSH 看不见的技能语法无关，
                # 否则 WorkBuddy 专属的下划线名技能会挂永久 warning（0/0 口径被打破）。
                warnings.append(f"dsh-name-grammar: {flavor} (name={fm.get('name')!r} 非 kebab-case，DSH 会静默忽略)")
            if not fm.get("description"):
                errors.append(f"empty-description: {flavor}")
            with open(sk, encoding="utf-8", errors="replace") as fh:
                miss = missing_refs(flavor, fh.read())
            if miss:
                known = external_refs(flavor)
                unack = [x for x in miss if x not in known]
                if unack:
                    warns.append((flavor, unack))
                else:
                    extrefs.append((flavor, len(miss)))
        for tool, flavor in sorted(ent["targets"].items()):
            seen.add((tool, name))
            link = os.path.join(CTX["tools"][tool], name)
            target = os.path.join(CTX["skills"], flavor)
            if not os.path.lexists(link):
                errors.append(f"missing: {tool}/{name}")
                continue
            if not is_linklike(link):
                errors.append(f"not-a-link: {tool}/{name}")
                continue
            if not same_path(os.path.realpath(link), target):
                errors.append(f"wrong-target: {tool}/{name} -> {os.path.realpath(link)}")
                continue
            want = ent["flavors"][flavor]["hash"]
            got = tree_hash(link)
            if got != want:
                errors.append(f"hash-mismatch: {tool}/{name} ({got[:8]} != {want[:8]})")
    excluded = {(e["tool"], e["name"]) for e in m.get("excluded", [])}
    for tool in TOOL_ORDER:
        for name, rec in scan_tool(tool).items():
            if (tool, name) in seen or (tool, name) in excluded or name.startswith(EXCLUDE_PREFIX):
                continue
            reason = "external-link" if rec["link"] else "unmanaged"
            warnings.append(f"{reason}: {tool}/{name}")
    for flavor, n in extrefs:
        log(f"  ext-ref: {flavor} ({n}) 由项目侧提供，见 references/SCRIPT_INVENTORY.md")
    for flavor, miss in sorted(warns):
        head = ", ".join(miss[:3]) + (f" …(+{len(miss) - 3})" if len(miss) > 3 else "")
        warnings.append(f"missing-ref: {flavor} ({len(miss)}): {head}")
    for w in warnings:
        log(f"  warn {w}")
    for e in errors:
        log(f"  FAIL {e}")
    log(f"verify: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


# ---------------------------------------------------------------- status

def cmd_status(args):
    m = load_manifest()
    log(f"{'skill':34s}" + "".join(f"{t:22s}" for t in TOOL_ORDER))
    for name, ent in sorted(m["skills"].items()):
        row = []
        for tool in TOOL_ORDER:
            f = ent["targets"].get(tool, "-")
            row.append(f if len(f) <= 21 else f[:18] + "...")
        log(f"{name:34s}" + "".join(f"{x:22s}" for x in row))
    dupes = 0
    for name, ent in m["skills"].items():
        hashes = [v["hash"] for v in ent["flavors"].values()]
        if len(hashes) > len(set(hashes)):
            dupes += 1
    log(f"\n{len(m['skills'])} skills / {sum(len(v['flavors']) for v in m['skills'].values())} flavors; "
        f"{dupes} skill(s) have identical flavors (unifiable)")


# ---------------------------------------------------------------- unify

def cmd_unify(args):
    m = load_manifest()
    ent = m["skills"].get(args.name)
    if not ent:
        raise SystemExit(f"unknown skill: {args.name}")
    if args.to not in ent["flavors"]:
        raise SystemExit(f"unknown flavor: {args.to}; choose from {list(ent['flavors'])}")
    for tool in ent["targets"]:
        ent["targets"][tool] = args.to
    save_manifest(m)
    log(f"unify {args.name} -> {args.to}; run: skillctl.py link --only {args.name}")


# ---------------------------------------------------------------- prune

def _git_has_history(repo, rel):
    """该路径在 git 历史里是否有记录（用于判断删除是否可恢复）。"""
    try:
        out = subprocess.run(["git", "-C", repo, "log", "--oneline", "--", rel],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def cmd_prune(args):
    """清理无 targets 指向的孤儿 flavor（unify 后残留）。

    默认只列出；--apply 才删除目录并从 manifest 移除。
    孤儿 flavor 会让 drift 持续报噪音，并使 manifest 的 flavor 数与实际使用不符。
    删除前校验 git 历史：无历史则拒绝，避免丢掉未提交内容。
    """
    m = load_manifest()
    orphans = []
    for name, ent in sorted(m["skills"].items()):
        used = set(ent["targets"].values())
        for flavor in sorted(ent["flavors"]):
            if flavor not in used:
                orphans.append((name, flavor))
    if not orphans:
        log("prune: 无孤儿 flavor")
        return 0
    for name, flavor in orphans:
        log(f"  {name}: {flavor}")
    if not args.apply:
        log(f"prune: {len(orphans)} 个孤儿 flavor（dry-run；加 --apply 删除）")
        return 0
    repo = CTX["repo"]
    is_git = os.path.isdir(os.path.join(repo, ".git"))
    removed, kept, gone = 0, [], []
    for name, flavor in orphans:
        d = os.path.join(CTX["skills"], flavor)
        if not os.path.isdir(d):
            gone.append((name, flavor))
            continue
        if not is_under(d, CTX["skills"]):
            kept.append((flavor, "仓外路径"))
            continue
        if is_git and not _git_has_history(repo, f"skills/{flavor}"):
            kept.append((flavor, "git 无历史，删除不可恢复"))
            continue
        shutil.rmtree(d)
        del m["skills"][name]["flavors"][flavor]
        removed += 1
    for name, flavor in gone:
        # 目录已不在（可能被外部清理）：记录留着会让 verify 一直报 no-SKILL.md
        m["skills"][name]["flavors"].pop(flavor, None)
    for name in list(m["skills"]):
        if not m["skills"][name]["flavors"]:
            del m["skills"][name]
    save_manifest(m)
    for flavor, why in kept:
        log(f"  ! 保留 {flavor}: {why}")
    for name, flavor in gone:
        log(f"  - 清理缺失记录 {flavor}")
    log(f"prune: 删除 {removed} 个孤儿 flavor"
        + (f"，清理 {len(gone)} 条缺失记录" if gone else "")
        + (f"，保留 {len(kept)} 个" if kept else ""))
    return 0


# ---------------------------------------------------------------- rollback

def cmd_rollback(args):
    root = CTX["backup"]
    if not os.path.isdir(root):
        raise SystemExit("没有备份目录")
    stamps = sorted(os.listdir(root))
    if not stamps:
        raise SystemExit("备份为空")
    ts = args.ts or stamps[-1]
    base = os.path.join(root, ts)
    if not os.path.isdir(base):
        raise SystemExit(f"unknown backup ts: {ts}")
    restored = 0
    for tool in sorted(os.listdir(base)):
        tdir = os.path.join(base, tool)
        if not os.path.isdir(tdir):
            continue
        for name in sorted(os.listdir(tdir)):
            src = os.path.join(tdir, name)
            dst = os.path.join(CTX["tools"][tool], name)
            if os.path.lexists(dst):
                if not is_linklike(dst) or not is_under(os.path.realpath(dst), CTX["skills"]):
                    raise SystemExit(f"refusing to overwrite non-managed dir: {dst}")
                remove_junction(dst)
            shutil.move(src, dst)
            restored += 1
            log(f"  restore {tool}/{name}")
    log(f"rollback {ts}: {restored} restored")


# ---------------------------------------------------------------- diff / report

def _read_skill_md(flavor):
    p = os.path.join(CTX["skills"], flavor, "SKILL.md")
    if not os.path.exists(p):
        return []
    return open(p, encoding="utf-8", errors="replace").read().splitlines()


def _split_fm(text):
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    return text[3:end], text[end + 4:]


def _where_differs(base_fm, base_body, fm, body):
    fm_same, body_same = base_fm == fm, base_body == body
    if fm_same and body_same:
        return "无"
    if body_same:
        return "仅frontmatter"
    if fm_same:
        return "仅正文"
    return "两者"


def cmd_diff(args):
    m = load_manifest()
    ent = m["skills"].get(args.name)
    if not ent:
        raise SystemExit(f"unknown skill: {args.name}")
    flavors = sorted(ent["flavors"])
    if len(flavors) == 1:
        log(f"{args.name}: 只有 1 个 flavor ({flavors[0]})")
        return
    base = args.base or flavors[0]
    if base not in flavors:
        raise SystemExit(f"unknown base flavor: {base}")
    for other in flavors:
        if other == base:
            continue
        log(f"\n=== {base} -> {other}")
        diff = list(difflib.unified_diff(_read_skill_md(base), _read_skill_md(other),
                                         fromfile=base, tofile=other, lineterm="", n=args.context))
        for line in diff[:args.lines]:
            log(line)
        if len(diff) > args.lines:
            log(f"... ({len(diff) - args.lines} more lines)")


def cmd_report(args):
    m = load_manifest()
    rows, multi, kinds = [], 0, {}
    for name, ent in sorted(m["skills"].items()):
        flavors = sorted(ent["flavors"])
        if len(flavors) < 2:
            continue
        multi += 1
        base = "\n".join(_read_skill_md(flavors[0]))
        base_fm, base_body = _split_fm(base)
        for f in flavors:
            lines = _read_skill_md(f)
            text = "\n".join(lines)
            fm, body = _split_fm(text)
            ratio = difflib.SequenceMatcher(None, base, text).ratio()
            changed = sum(1 for d in difflib.unified_diff(
                base.splitlines(), lines, lineterm="", n=0)
                if d[:1] in "+-" and not d.startswith(("+++", "---")))
            where = _where_differs(base_fm, base_body, fm, body)
            if f != flavors[0]:
                kinds[where] = kinds.get(where, 0) + 1
            marks = []
            for token in ("WorkBuddy", "CodeBuddy", "Qoder", "OpenCode", "Claude"):
                n = text.count(token)
                if n:
                    marks.append(f"{token}x{n}")
            users = [t for t, x in ent["targets"].items() if x == f]
            rows.append((name, f, len(lines), ",".join(users),
                         f"{ratio:.1%}", changed, where, " ".join(marks)))
    out = [f"# Flavor 差异清单（{time.strftime('%Y-%m-%d %H:%M')}）", "",
           f"共 {len(m['skills'])} 个 skill / {sum(len(v['flavors']) for v in m['skills'].values())} 个 flavor；"
           f"{multi} 个 skill 存在多版本。", "",
           "「相似度/变更行」以该 skill 的首个 flavor 为基准；「差异位置」区分 frontmatter 与正文，"
           "仅 frontmatter 不同说明内容同源、可直接统一。", "",
           "| skill | flavor | SKILL.md 行数 | 使用工具 | 相似度 | 变更行 | 差异位置 | 工具专属词 |",
           "|---|---|---:|---|---:|---:|---|---|"]
    for r in rows:
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(*r))
    p = os.path.join(CTX["repo"], "_state", "flavor_report.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    log(f"report: {multi} multi-flavor skills -> {p}")
    log("  variant diff location: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))


# ---------------------------------------------------------------- secrets

SECRET_PATTERNS = [
    # 现代 key 含 - 与 _（sk-proj-... / sk-ant-...），不能只认 [A-Za-z0-9]
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|access[_-]?token|password)\b\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]"),
    # .env 写法不戴引号：NAME_WITH_KEY=value
    re.compile(r"(?i)^\s*[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*\s*[:=]\s*([A-Za-z0-9_\-.\.]{20,})\s*$"),
    re.compile(r"(?i)Bearer\s+([A-Za-z0-9_\-.\.]{20,})"),
]
SCAN_SUFFIXES = (".md", ".py", ".json", ".yaml", ".yml", ".js", ".ts", ".sh", ".txt", ".env")


def committable_files(repo):
    """返回 git 会纳入版本控制的文件（相对路径）；非 git 仓或 git 不可用返回 None。

    只扫会提交的文件：被 .gitignore 挡住的 .env 不该让 check-secrets 永远红；
    而它一旦没被挡住，就必须被抓出来。
    """
    try:
        out = subprocess.run(
            ["git", "-C", repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [x for x in out.stdout.decode("utf-8", "replace").split("\0") if x]


def cmd_check_secrets(args):
    repo = CTX["repo"]
    files = committable_files(repo)
    if files is None:
        files = []
        for dp, dn, fn in os.walk(repo):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            files += [os.path.relpath(os.path.join(dp, f), repo) for f in fn]
    hits = scanned = 0
    for rel in files:
        if not rel.lower().endswith(SCAN_SUFFIXES):
            continue
        p = os.path.join(repo, rel)
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        scanned += 1
        for i, line in enumerate(text.splitlines(), 1):
            if any(x in line for x in ("getenv", "environ", "$", "{", "<", "your_", "example")):
                continue
            for pat in SECRET_PATTERNS:
                if pat.search(line):
                    masked = re.sub(r"[A-Za-z0-9_\-]{16,}",
                                    lambda m: m.group()[:3] + "***", line.strip())
                    log(f"  {rel}:{i}: {masked[:120]}")
                    hits += 1
                    break
    log(f"check-secrets: {hits} hit(s) in {scanned} file(s) git would commit")
    return 1 if hits else 0


# ---------------------------------------------------------------- selftest

def cmd_selftest(args):
    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))

    saved = dict(CTX)
    tmp = tempfile.mkdtemp(prefix="skillctl-test-")
    try:
        tools = {t: os.path.join(tmp, "tools", t) for t in TOOL_ORDER}
        t0, t1, t2 = TOOL_ORDER[0], TOOL_ORDER[2], TOOL_ORDER[1]
        CTX.update({
            "repo": os.path.join(tmp, "repo"),
            "skills": os.path.join(tmp, "repo", "skills"),
            "manifest": os.path.join(tmp, "repo", "manifest.json"),
            "backup": os.path.join(tmp, "backup"),
            "tools": tools,
        })
        os.makedirs(CTX["skills"])
        for t in TOOL_ORDER:
            os.makedirs(os.path.join(tools[t], "same"), exist_ok=True)
            _write(os.path.join(tools[t], "same", "SKILL.md"),
                   "---\nname: same\ndescription: same demo\n---\nbody\n")
        _write(os.path.join(tools[t0], "diff", "SKILL.md"),
               "---\nname: diff\ndescription: diff demo\n---\nwb\n")
        _write(os.path.join(tools[t1], "diff", "SKILL.md"),
               "---\nname: diff\ndescription: diff demo\n---\noc\n")
        _write(os.path.join(tools[t2], "onlyq", "SKILL.md"),
               "---\nname: onlyq\ndescription: onlyq demo\n---\nq\n")
        os.makedirs(os.path.join(tools[t1], "_skip"))
        _write(os.path.join(tools[t1], "_skip", "SKILL.md"), "x\n")

        cmd_import(argparse.Namespace())
        m = load_manifest()
        chk("import counts", set(m["skills"]) == {"same", "diff", "onlyq"})
        chk("same one flavor", list(m["skills"]["same"]["flavors"]) == ["same"])
        chk("diff two flavors", set(m["skills"]["diff"]["flavors"]) == {"diff", "diff__opencode"})
        chk("onlyq single target", m["skills"]["onlyq"]["targets"] == {t2: "onlyq"})
        chk("excluded backup", any(e["name"] == "_skip" for e in m["excluded"]))

        cmd_link(argparse.Namespace(only=None, dry_run=False, ts=None))
        for t in TOOL_ORDER:
            chk(f"junction {t}/same", is_linklike(os.path.join(tools[t], "same")))
        chk("content readable", open(os.path.join(tools[t1], "same", "SKILL.md"), encoding="utf-8").read().endswith("body\n"))
        chk("variant kept", "oc" in open(os.path.join(tools[t1], "diff", "SKILL.md"), encoding="utf-8").read())
        chk("verify clean", cmd_verify(argparse.Namespace()) == 0)

        # 模拟漂移：把链接换成内容不同的真实目录
        j = os.path.join(tools[t2], "same")
        remove_junction(j)
        os.makedirs(j)
        _write(os.path.join(j, "SKILL.md"), "---\nname: same\ndescription: same demo\n---\ntampered\n")
        chk("verify detects drift", cmd_verify(argparse.Namespace()) == 1)
        shutil.rmtree(j)
        cmd_link(argparse.Namespace(only=None, dry_run=False, ts=None))
        chk("relink restores", cmd_verify(argparse.Namespace()) == 0)

        cmd_refresh(argparse.Namespace())
        chk("refresh no-op", cmd_verify(argparse.Namespace()) == 0)

        # generated_at 必须随每次写 manifest 更新，否则它会静默过期
        before = load_manifest().get("generated_at")
        time.sleep(1.1)
        save_manifest(load_manifest())
        chk("save_manifest updates generated_at", load_manifest().get("generated_at") != before)

        # normalize-eol：CRLF 混用必须被改写成 LF，且正文字节不变
        pe = os.path.join(CTX["skills"], "same", "SKILL.md")
        with open(pe, "wb") as fh:
            fh.write(b"---\r\nname: same\r\ndescription: same demo\r\n---\r\nbody\r\n")
        cmd_normalize_eol(argparse.Namespace())
        chk("normalize-eol strips CRLF", check_eol("same", pe) == (0, 5))
        chk("normalize-eol keeps body", open(pe, encoding="utf-8").read().endswith("body\n"))
        # 行尾也是内容字节的一部分：改完必须 refresh，否则 verify 会报 hash-mismatch
        cmd_refresh(argparse.Namespace())
        chk("refresh after normalize-eol", cmd_verify(argparse.Namespace()) == 0)

        # drift：正文差异大的两个 flavor 要被抓出来，完全相同的要放过
        d1 = os.path.join(CTX["skills"], "drift")
        _write(os.path.join(d1, "SKILL.md"),
               "---\nname: drift\ndescription: drift demo\n---\n" + "alpha\n" * 40)
        d2 = os.path.join(CTX["skills"], "drift__opencode")
        _write(os.path.join(d2, "SKILL.md"),
               "---\nname: drift\ndescription: drift demo\n---\n" + "bravo\n" * 40)
        d3 = os.path.join(CTX["skills"], "twin")
        _write(os.path.join(d3, "SKILL.md"),
               "---\nname: twin\ndescription: twin demo\n---\nidentical body\n")
        d4 = os.path.join(CTX["skills"], "twin__opencode")
        _write(os.path.join(d4, "SKILL.md"),
               "---\nname: twin\ndescription: twin demo\n---\nidentical body\n")
        md = load_manifest()
        for nm, fl in (("drift", ("drift", "drift__opencode")), ("twin", ("twin", "twin__opencode"))):
            md["skills"][nm] = {"flavors": {f: {"hash": tree_hash(os.path.join(CTX["skills"], f)),
                                                "files": count_files(os.path.join(CTX["skills"], f))}
                                           for f in fl},
                                "targets": {}}
        save_manifest(md)
        buf = []
        orig_log = globals()["log"]
        globals()["log"] = lambda s: buf.append(str(s))
        try:
            cmd_drift(argparse.Namespace(min_ratio=0.85))
        finally:
            globals()["log"] = orig_log
        dtxt = "\n".join(buf)
        chk("drift flags divergent flavor", "drift__opencode" in dtxt)
        chk("drift passes identical flavor", "twin__opencode" not in dtxt)
        chk("drift shows diff location", "(两者)" in dtxt or "(仅正文)" in dtxt
            or "(仅frontmatter)" in dtxt)
        for nm in ("drift", "twin"):
            del md["skills"][nm]
            shutil.rmtree(os.path.join(CTX["skills"], nm), ignore_errors=True)
            shutil.rmtree(os.path.join(CTX["skills"], nm + "__opencode"), ignore_errors=True)
        save_manifest(md)

        # prune：孤儿 flavor 可清理；dry-run 不删，--apply 才删
        o1 = os.path.join(CTX["skills"], "orphan")
        o2 = os.path.join(CTX["skills"], "orphan__opencode")
        _write(os.path.join(o1, "SKILL.md"), "---\nname: orphan\ndescription: orphan demo\n---\nbody\n")
        _write(os.path.join(o2, "SKILL.md"), "---\nname: orphan\ndescription: orphan demo\n---\nold\n")
        mp = load_manifest()
        mp["skills"]["orphan"] = {"flavors": {
            "orphan": {"hash": tree_hash(o1), "files": count_files(o1)},
            "orphan__opencode": {"hash": tree_hash(o2), "files": count_files(o2)}},
            "targets": {t2: "orphan"}}
        save_manifest(mp)

        def prune_out(apply):
            buf = []
            orig_log = globals()["log"]
            globals()["log"] = lambda s: buf.append(str(s))
            try:
                cmd_prune(argparse.Namespace(apply=apply))
            finally:
                globals()["log"] = orig_log
            return "\n".join(buf)

        ptxt = prune_out(False)
        chk("prune dry-run lists orphan", "orphan__opencode" in ptxt)
        chk("prune dry-run keeps dir", os.path.isdir(o2))
        prune_out(True)
        chk("prune --apply removes dir", not os.path.isdir(o2))
        chk("prune --apply updates manifest",
            "orphan__opencode" not in load_manifest()["skills"]["orphan"]["flavors"])
        chk("prune --apply keeps live flavor", os.path.isdir(o1))
        # 目录已缺失时记录也必须清掉，否则 verify 会一直报 no-SKILL.md
        mp = load_manifest()
        mp["skills"]["orphan"]["flavors"]["orphan__opencode"] = {"hash": "y" * 64, "files": 1}
        save_manifest(mp)
        prune_out(True)
        chk("prune cleans record for missing dir",
            "orphan__opencode" not in load_manifest()["skills"]["orphan"]["flavors"])
        shutil.rmtree(o1, ignore_errors=True)
        mp = load_manifest()
        del mp["skills"]["orphan"]
        save_manifest(mp)

        # 外部引用：SCRIPT_INVENTORY 登记后只记 ext-ref 信息行，注销即重新告警
        gf = os.path.join(CTX["skills"], "gap")
        _write(os.path.join(gf, "SKILL.md"),
               "---\nname: gap\ndescription: gap demo\n---\nrun `scripts/ghost.py`\n")

        def verify_out():
            buf = []
            orig_log = globals()["log"]
            globals()["log"] = lambda s: buf.append(str(s))
            try:
                rc = cmd_verify(argparse.Namespace())
            finally:
                globals()["log"] = orig_log
            return rc, "\n".join(buf)

        mg = load_manifest()
        mg["skills"]["gap"] = {"flavors": {"gap": {"hash": tree_hash(gf), "files": count_files(gf)}},
                              "targets": {}}
        save_manifest(mg)
        rc, out = verify_out()
        chk("verify flags unregistered ref", "missing-ref: gap" in out)
        _write(os.path.join(gf, "references", "SCRIPT_INVENTORY.md"), "| `ghost.py` | x | y |\n")
        rc, out = verify_out()
        chk("verify marks external ref", "missing-ref: gap" not in out and "ext-ref: gap" in out)
        mg = load_manifest()
        del mg["skills"]["gap"]
        save_manifest(mg)
        shutil.rmtree(gf)

        # exclude：登记「已知但不纳入」的目录后，verify 从「遗漏告警」转为「已决策」
        os.makedirs(os.path.join(tools[t2], "market"), exist_ok=True)
        _write(os.path.join(tools[t2], "market", "SKILL.md"),
               "---\nname: market\ndescription: market demo\n---\nx\n")
        rc, out = verify_out()
        chk("verify flags unmanaged", f"unmanaged: {t2}/market" in out)
        # exclude 校验：名字不存在、真实目录、已纳管 都要拒绝，避免登记出「幻觉条目」
        try:
            cmd_exclude(argparse.Namespace(tool=t2, name="ghost", reason="x"))
            chk("exclude rejects missing dir", False)
        except SystemExit:
            chk("exclude rejects missing dir", True)
        try:
            cmd_exclude(argparse.Namespace(tool=t2, name="same", reason="x"))
            chk("exclude rejects managed skill", False)
        except SystemExit:
            chk("exclude rejects managed skill", True)
        cmd_exclude(argparse.Namespace(tool=t2, name="market", reason="marketplace-managed"))
        rc, out = verify_out()
        chk("exclude silences unmanaged", f"unmanaged: {t2}/market" not in out)
        try:
            cmd_exclude(argparse.Namespace(tool=t2, name="market", reason="x"))
            chk("exclude rejects duplicate", False)
        except SystemExit:
            chk("exclude rejects duplicate", True)

        # add：新增 skill 并链接；非法输入回滚
        src = os.path.join(tmp, "src", "fresh")
        _write(os.path.join(src, "SKILL.md"), "---\nname: fresh\ndescription: demo\n---\nfresh body\n")
        ns = argparse.Namespace(name="fresh", source=src, empty=False, flavor=None,
                                tools=t2, no_link=False)
        chk("add rc", cmd_add(ns) == 0)
        chk("add linked", is_linklike(os.path.join(tools[t2], "fresh")))
        chk("add only listed tool", not os.path.lexists(os.path.join(tools[t0], "fresh")))
        chk("add verify clean", cmd_verify(argparse.Namespace()) == 0)

        bad = os.path.join(tmp, "src", "bad")
        _write(os.path.join(bad, "SKILL.md"), "---\nname: other\ndescription: x\n---\n")
        try:
            cmd_add(argparse.Namespace(name="bad", source=bad, empty=False, flavor=None,
                                       tools=t2, no_link=True))
            chk("add rejects name mismatch", False)
        except SystemExit:
            chk("add rejects name mismatch", not os.path.isdir(os.path.join(CTX["skills"], "bad")))
        try:
            cmd_add(argparse.Namespace(name="Bad_Name", source=src, empty=False, flavor=None,
                                       tools=t2, no_link=True))
            chk("add rejects bad slug", False)
        except SystemExit:
            chk("add rejects bad slug", True)

        # dsh-name-grammar：只对 targets.dsh 指向的 flavor 告警——DSH 看不见的
        # 技能（如 WorkBuddy 专属的下划线名）语法无关，永久 warning 会打破 0/0 口径
        wk = os.path.join(CTX["skills"], "under_score")
        _write(os.path.join(wk, "SKILL.md"),
               "---\nname: under_score\ndescription: wb only\n---\nx\n")
        mu = load_manifest()
        mu["skills"]["under_score"] = {
            "flavors": {"under_score": {"hash": tree_hash(wk), "files": count_files(wk)}},
            "targets": {"workbuddy": "under_score"}}
        save_manifest(mu)
        cmd_link(argparse.Namespace(only="under_score", ts=None, dry_run=False))
        rc, out = verify_out()
        chk("verify silent for non-kebab without dsh target", "dsh-name-grammar" not in out)
        # 真给 DSH 部署时（绕过 link 政策手工建链），verify 必须兜住
        mu = load_manifest()
        mu["skills"]["under_score"]["targets"][TOOL_ORDER[-1]] = "under_score"
        save_manifest(mu)
        chk("manual dsh deploy links",
            _link_one("selftest", TOOL_ORDER[-1], "under_score",
                      load_manifest()["skills"]["under_score"], "under_score", False) == "linked")
        rc, out = verify_out()
        chk("verify warns for non-kebab with dsh target",
            "dsh-name-grammar: under_score" in out)
        # link 是部署闸门：非 kebab 名不准上 dsh（cmd_add 拦 add，link 拦部署）
        chk("link refuses dsh target for non-kebab",
            cmd_link(argparse.Namespace(only="under_score", ts=None, dry_run=False)) == 1)
        mu = load_manifest()
        del mu["skills"]["under_score"]
        save_manifest(mu)
        shutil.rmtree(wk, ignore_errors=True)
        for _t in (TOOL_ORDER[0], TOOL_ORDER[-1]):
            _p = os.path.join(tools[_t], "under_score")
            if os.path.lexists(_p):
                remove_junction(_p)

        # verify 内容层：frontmatter name 与目录不一致必须报错
        p = os.path.join(CTX["skills"], "fresh", "SKILL.md")
        _write(p, "---\nname: wrong\ndescription: demo\n---\nfresh body\n")
        cmd_refresh(argparse.Namespace())
        chk("verify detects name mismatch", cmd_verify(argparse.Namespace()) == 1)
        os.remove(p)
        cmd_refresh(argparse.Namespace())
        chk("verify detects missing SKILL.md", cmd_verify(argparse.Namespace()) == 1)

        cmd_rollback(argparse.Namespace(ts=None))
        for t in TOOL_ORDER:
            p = os.path.join(tools[t], "same")
            chk(f"rollback real dir {t}", os.path.isdir(p) and not is_linklike(p))
            chk(f"rollback content {t}", open(os.path.join(p, "SKILL.md"), encoding="utf-8").read().endswith("body\n"))
        chk("rollback diff wb", open(os.path.join(tools[t0], "diff", "SKILL.md"), encoding="utf-8").read().endswith("wb\n"))
        chk("rollback diff oc", open(os.path.join(tools[t1], "diff", "SKILL.md"), encoding="utf-8").read().endswith("oc\n"))
        # check-secrets：.env 的无引号写法要认得（现代 key 含 -/_，旧正则漏判）
        probe = os.path.join(CTX["repo"], "probe_cred.env")
        _write(probe, "DEMO_API_KEY=sk-proj-" + "A" * 30 + "\n")
        buf = []
        orig_log = globals()["log"]
        globals()["log"] = lambda s: buf.append(str(s))
        try:
            rc = cmd_check_secrets(argparse.Namespace())
        finally:
            globals()["log"] = orig_log
        chk("check-secrets flags unquoted env key", rc == 1 and "probe_cred.env" in "\n".join(buf))
        os.remove(probe)
    finally:
        CTX.clear()
        CTX.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        log(f"  {'PASS' if ok else 'FAIL'} {n}")
    log(f"selftest: {len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


# ---------------------------------------------------------------- main

def cmd_init(args):
    """Create the repo skeleton: skills/, manifest.json and tools.json."""
    os.makedirs(CTX["skills"], exist_ok=True)
    if not os.path.isfile(CTX["manifest"]):
        save_manifest({"schema": 1, "generated_at": "", "skills": {}, "excluded": []})
        log("created %s" % CTX["manifest"])
    else:
        log("kept existing %s" % CTX["manifest"])
    cfg = os.path.join(CTX["repo"], "tools.json")
    if not os.path.isfile(cfg):
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_TOOLS, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        log("created %s" % cfg)
    else:
        log("kept existing %s" % cfg)
    log("next: python skillctl.py add <name> --empty  (or import)")
    return 0


def cmd_doctor(args):
    """Diagnose the repo, every configured tool directory and the link state."""
    errors = warnings = oks = 0
    if not os.path.isdir(CTX["repo"]):
        log("[ERROR] repo directory is missing: %s" % CTX["repo"])
        return 1
    try:
        load_manifest()
        oks += 1
        log("[OK] manifest parses")
    except Exception as exc:
        log("[ERROR] manifest: %s" % exc)
        return 1
    for tool, root in CTX["tools"].items():
        if not os.path.isdir(root):
            warnings += 1
            log("[WARN] %s: skills directory missing (%s)" % (tool, root))
        elif not os.access(root, os.W_OK):
            warnings += 1
            log("[WARN] %s: skills directory is not writable" % tool)
        else:
            oks += 1
            log("[OK] %s: %s" % (tool, root))
    rc = cmd_verify(argparse.Namespace())
    if rc:
        errors += 1
    else:
        oks += 1
    wb_root = CTX["tools"].get("workbuddy")
    if wb_root:
        cache = os.path.join(os.path.dirname(wb_root), ".skill-list-cache.json")
        if os.path.isfile(cache):
            newest = 0
            for dp, _dn, fn in os.walk(CTX["skills"]):
                for f in fn:
                    if f == "SKILL.md":
                        newest = max(newest, os.path.getmtime(os.path.join(dp, f)))
            if os.path.getmtime(cache) < newest:
                warnings += 1
                log("[WARN] WorkBuddy skill cache is older than SKILL.md changes; restart WorkBuddy")
            else:
                oks += 1
                log("[OK] WorkBuddy skill cache is current")
    log("doctor: %d ok, %d warn, %d error" % (oks, warnings, errors))
    return 1 if errors else 0


def cmd_adopt(args):
    """Register a new tool (agent) in tools.json, optionally linking right away."""
    tool = (args.tool or "").strip()
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", tool):
        log("[ERROR] tool name must be lowercase letters, digits, dash or underscore")
        return 1
    root = os.path.expanduser((args.path or "").strip())
    if not os.path.isabs(root):
        log("[ERROR] --path must be absolute or start with ~")
        return 1
    if not os.path.isdir(root):
        if not args.create:
            log("[ERROR] path does not exist: %s (add --create)" % root)
            return 1
        os.makedirs(root, exist_ok=True)
    tools = load_tools(CTX["repo"])
    tools[tool] = root
    path = os.path.join(CTX["repo"], "tools.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(tools, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    CTX["tools"][tool] = root
    global TOOL_ORDER
    TOOL_ORDER = tuple(CTX["tools"].keys())
    log("adopted %s -> %s" % (tool, root))
    if args.link:
        return cmd_link(argparse.Namespace(only=None, dry_run=False, ts=None))
    log("next: python skillctl.py link --only <skill>")
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="skillctl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("import").set_defaults(fn=cmd_import)

    p_add = sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("--from", dest="source")
    p_add.add_argument("--empty", action="store_true")
    p_add.add_argument("--flavor")
    p_add.add_argument("--tools", help="comma-separated tool names; default is every configured tool")
    p_add.add_argument("--no-link", action="store_true")
    p_add.set_defaults(fn=cmd_add)

    p_ex = sub.add_parser("exclude")
    p_ex.add_argument("name")
    p_ex.add_argument("--tool", required=True)
    p_ex.add_argument("--reason", required=True)
    p_ex.set_defaults(fn=cmd_exclude)

    sub.add_parser("refresh").set_defaults(fn=cmd_refresh)

    p_link = sub.add_parser("link")
    p_link.add_argument("--only")
    p_link.add_argument("--dry-run", action="store_true")
    p_link.add_argument("--ts")
    p_link.set_defaults(fn=cmd_link)

    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    p_unify = sub.add_parser("unify")
    p_unify.add_argument("name")
    p_unify.add_argument("--to", required=True)
    p_unify.set_defaults(fn=cmd_unify)

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--apply", action="store_true")
    p_prune.set_defaults(fn=cmd_prune)

    p_rb = sub.add_parser("rollback")
    p_rb.add_argument("ts", nargs="?")
    p_rb.set_defaults(fn=cmd_rollback)

    p_diff = sub.add_parser("diff")
    p_diff.add_argument("name")
    p_diff.add_argument("--base")
    p_diff.add_argument("--context", type=int, default=2)
    p_diff.add_argument("--lines", type=int, default=120)
    p_diff.set_defaults(fn=cmd_diff)

    sub.add_parser("report").set_defaults(fn=cmd_report)

    sub.add_parser("check-secrets").set_defaults(fn=cmd_check_secrets)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)

    sub.add_parser("normalize-eol").set_defaults(fn=cmd_normalize_eol)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)

    p_adopt = sub.add_parser("adopt")
    p_adopt.add_argument("--tool", required=True)
    p_adopt.add_argument("--path", required=True)
    p_adopt.add_argument("--create", action="store_true")
    p_adopt.add_argument("--link", action="store_true")
    p_adopt.set_defaults(fn=cmd_adopt)

    p_drift = sub.add_parser("drift")
    p_drift.add_argument("--min-ratio", type=float, default=0.85)
    p_drift.set_defaults(fn=cmd_drift)

    args = ap.parse_args()
    rc = args.fn(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
