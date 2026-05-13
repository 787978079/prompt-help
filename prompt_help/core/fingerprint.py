"""项目指纹：从 cwd 推断 (langs, frameworks, keywords)，用 Jaccard 算相似度。

MVP 用 token 集合 + 标签重合，可解释易调试。
embedding 留到 Phase 3 当 token-overlap 在真实场景出现召回缺漏时再上。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class Fingerprint:
    project_name: str
    langs: set[str] = field(default_factory=set)
    frameworks: set[str] = field(default_factory=set)
    keywords: set[str] = field(default_factory=set)

    def all_tokens(self) -> set[str]:
        return self.langs | self.frameworks | self.keywords


_LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php",
    ".c": "c", ".cpp": "cpp", ".h": "c",
    ".cs": "csharp", ".swift": "swift",
}


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "are", "was", "were", "be", "been", "have", "has",
    "had", "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "must", "can", "this", "that", "these", "those", "it", "its", "as", "if",
    "then", "else", "than", "such", "no", "not", "only", "own", "same", "so",
    "all", "each", "any", "some", "both", "more", "most", "other", "into", "you",
    "your", "we", "our", "i", "my", "they", "their", "他", "她", "它", "的",
    "是", "在", "了", "和", "与", "或", "但", "也", "还", "都", "一", "这",
    "那", "个", "有", "为", "对", "以", "及", "等", "上", "下", "中", "从",
}


def fingerprint(cwd: Path) -> Fingerprint:
    cwd = cwd.resolve()
    fp = Fingerprint(project_name=cwd.name)
    _add_langs_from_files(cwd, fp)
    _add_from_package_json(cwd, fp)
    _add_from_pyproject(cwd, fp)
    _add_from_requirements(cwd, fp)
    _add_from_cargo(cwd, fp)
    _add_from_gomod(cwd, fp)
    _add_keywords_from_readme(cwd, fp)
    return fp


def _add_langs_from_files(cwd: Path, fp: Fingerprint) -> None:
    skip = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
    counts: dict[str, int] = {}
    try:
        for p in cwd.iterdir():
            if p.name in skip:
                continue
            if p.is_file():
                lang = _LANG_BY_EXT.get(p.suffix.lower())
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1
            elif p.is_dir():
                # 浅扫描一层子目录
                for c in p.iterdir():
                    if c.is_file():
                        lang = _LANG_BY_EXT.get(c.suffix.lower())
                        if lang:
                            counts[lang] = counts.get(lang, 0) + 1
    except (PermissionError, OSError):
        pass
    fp.langs.update(counts.keys())


def _add_from_package_json(cwd: Path, fp: Fingerprint) -> None:
    f = cwd / "package.json"
    if not f.is_file():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    fp.langs.add("javascript")
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    for name in deps.keys():
        fp.frameworks.add(name.lower())
    if "next" in deps:
        fp.frameworks.add("nextjs")
    if "react" in deps:
        fp.frameworks.add("react")
    if "vue" in deps:
        fp.frameworks.add("vue")
    if any(k.startswith("typescript") or k == "typescript" for k in deps):
        fp.langs.add("typescript")


def _add_from_pyproject(cwd: Path, fp: Fingerprint) -> None:
    f = cwd / "pyproject.toml"
    if not f.is_file():
        return
    try:
        with f.open("rb") as fp_io:
            data = tomllib.load(fp_io)
    except Exception:
        return
    fp.langs.add("python")
    deps: list[str] = []
    deps += (data.get("project") or {}).get("dependencies") or []
    poetry_deps = (((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {})
    deps += list(poetry_deps.keys())
    for d in deps:
        name = re.split(r"[<>=!~\[ ]", str(d), maxsplit=1)[0].lower().strip()
        if name and name != "python":
            fp.frameworks.add(name)


def _add_from_requirements(cwd: Path, fp: Fingerprint) -> None:
    f = cwd / "requirements.txt"
    if not f.is_file():
        return
    fp.langs.add("python")
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~\[ ]", line, maxsplit=1)[0].lower().strip()
            if name:
                fp.frameworks.add(name)
    except Exception:
        return


def _add_from_cargo(cwd: Path, fp: Fingerprint) -> None:
    f = cwd / "Cargo.toml"
    if not f.is_file():
        return
    fp.langs.add("rust")
    try:
        with f.open("rb") as fp_io:
            data = tomllib.load(fp_io)
        for name in (data.get("dependencies") or {}).keys():
            fp.frameworks.add(name.lower())
    except Exception:
        return


def _add_from_gomod(cwd: Path, fp: Fingerprint) -> None:
    f = cwd / "go.mod"
    if not f.is_file():
        return
    fp.langs.add("go")
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([a-zA-Z0-9._/-]+)\s+v\d", line)
            if m:
                # 取最后一段作为简短 framework 名
                fp.frameworks.add(m.group(1).split("/")[-1].lower())
    except Exception:
        return


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}", re.UNICODE)
_CHN_RE = re.compile(r"[一-鿿]{2,}")


def _add_keywords_from_readme(cwd: Path, fp: Fingerprint, max_chars: int = 1500) -> None:
    for name in ("README.md", "README.MD", "README.rst", "README.txt", "README"):
        f = cwd / name
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")[:max_chars].lower()
            except Exception:
                return
            for w in _WORD_RE.findall(text):
                w = w.lower()
                if w not in _STOPWORDS and len(w) >= 4:
                    fp.keywords.add(w)
            for w in _CHN_RE.findall(text):
                if w not in _STOPWORDS and len(w) <= 6:
                    fp.keywords.add(w)
            return


def jaccard_similarity(a: Fingerprint, b: Fingerprint) -> float:
    s1 = a.all_tokens()
    s2 = b.all_tokens()
    if not s1 or not s2:
        return 0.0
    inter = s1 & s2
    union = s1 | s2
    return len(inter) / len(union) if union else 0.0


def overlap_coefficient(a: Fingerprint, b: Fingerprint) -> float:
    """asymmetric: |A ∩ B| / min(|A|, |B|)。

    比 Jaccard 更适合"小项目像不像大项目子集"的判断 ——
    当一个项目的指纹（含 README 关键词）远大于另一个时，Jaccard 会被分母拖低。
    """
    s1 = a.all_tokens()
    s2 = b.all_tokens()
    if not s1 or not s2:
        return 0.0
    inter = s1 & s2
    return len(inter) / min(len(s1), len(s2))


def to_dict(fp: Fingerprint) -> dict:
    return {
        "project_name": fp.project_name,
        "langs": sorted(fp.langs),
        "frameworks": sorted(fp.frameworks),
        "keywords": sorted(fp.keywords),
    }


def from_dict(d: dict) -> Fingerprint:
    return Fingerprint(
        project_name=str(d.get("project_name") or "?"),
        langs=set(d.get("langs") or []),
        frameworks=set(d.get("frameworks") or []),
        keywords=set(d.get("keywords") or []),
    )


def stack_overlap(prompt_stack: list[str], fp: Fingerprint) -> float:
    """提示词的 stack 字段与项目指纹的简单重合度（粗调匹配）。"""
    if not prompt_stack:
        return 0.0
    stack_set = {s.lower().strip() for s in prompt_stack if s.strip()}
    project_tokens = fp.all_tokens()
    inter = stack_set & project_tokens
    return len(inter) / len(stack_set) if stack_set else 0.0
