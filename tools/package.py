#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/package.py — 통합 문서화 (md + HTML · LLM 0콜)

게이트 통과본만 산출 영역(`out/<원천명>/`)에 닿는다. 판단이 없다 — 템플릿이다.

산출 3종
    안테나_통합문서.md   치환본 그대로(사람이 읽는 최종 문서)
    dossier.json         **데이터 계약** — HTML 테스트베드가 읽는 유일한 입력
    index.html           자기완결 대시보드(테스트베드가 이 자리를 대체한다)

왜 dossier.json 을 계약으로 못박나
    화면을 누가 만들든(테스트베드·검수 서버·다른 도구) **같은 파일 하나**만 읽으면 되게 한다.
    화면이 work/ 를 뒤지기 시작하면 산출 경로가 화면의 구현 세부에 묶인다.

CLI
    python tools/package.py build <run_id>
    python tools/package.py self-test
"""
from __future__ import annotations

import html as _html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C     # noqa: E402
import catalog as CAT   # noqa: E402
import docspec as DS    # noqa: E402
import gate as GATE     # noqa: E402

DOSSIER_VERSION = 1
DOC_NAME = "안테나_통합문서.md"
DOSSIER_NAME = "dossier.json"
HTML_NAME = "index.html"


def build_dossier(run_id: str, work: Path | None = None, spec: dict | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    spec = spec or DS.load()
    cat = CAT.load(run_id, work)
    verdict = C.read_json(work / GATE.VERDICT_NAME)
    if not verdict.get("pass"):
        raise ValueError(f"게이트 미통과본은 포장하지 않는다: {run_id}")
    doc_md = (work / GATE.SUBSTITUTED_NAME).read_text(encoding="utf-8")
    E = cat["entries"]

    def by_role(role):
        return next((e for e in E.values() if e.get("role") == role), None)

    tiles = []
    for t in (spec.get("html") or {}).get("tiles") or []:
        e = by_role(t["role"])
        tiles.append({"role": t["role"], "label": t["label"],
                      "value": (e or {}).get("render", ""),
                      "unit": (e or {}).get("unit", ""),
                      "present": bool(e and e.get("render"))})

    refs = verdict.get("refs_used") or []
    undeclared = [r for r in refs if not r.get("role_declared")]
    try:
        chk = C.read_json(work / "해석_결과.json")
    except Exception:
        chk = {}
    unjudged = [{"check": i.get("check"), "unit": i.get("unit"),
                 "reason": i.get("reason", "")}
                for i in (chk.get("items") or []) if i.get("pass") is None]

    render_art = []
    for p in sorted(work.rglob("*")):
        if p.suffix.lower() in (".svg", ".png", ".html") and p.is_file():
            if p.name in (HTML_NAME,):
                continue
            render_art.append({"name": p.name, "rel": str(p.relative_to(work)),
                               "kind": p.suffix.lower().lstrip(".")})

    badges = []
    for b in (spec.get("html") or {}).get("badges") or []:
        n = {"role_undeclared": len(undeclared),
             "exempt_applied": len(verdict.get("exempt_hits") or []),
             "unmapped_role": len(cat.get("unmapped_keys") or []),
             "unjudged": len(unjudged)}.get(b["id"], 0)
        badges.append({**b, "count": n, "active": n > 0})

    return {
        "dossier_version": DOSSIER_VERSION,
        "run_id": run_id,
        "source_name": (E.get("식별.원천명") or {}).get("render", run_id),
        "spec_version": spec.get("spec_version"),
        "rule_version": verdict.get("rule_version"),
        "generated_from": {"work": str(work), "catalog": cat["n_entries"]},
        "title": spec.get("html", {}).get("title", spec.get("title")),
        "tiles": tiles,
        "panels": (spec.get("html") or {}).get("panels") or [],
        "badges": badges,
        "doc_md": doc_md,
        "gate": {"pass": verdict.get("pass"), "n_refs": verdict.get("n_refs"),
                 "n_violations": len(verdict.get("violations") or []),
                 "violations": verdict.get("violations") or [],
                 "recorded": verdict.get("recorded")},
        "refs_used": refs,
        "role_undeclared": undeclared,
        "exempt_hits": verdict.get("exempt_hits") or [],
        "unmapped_keys": cat.get("unmapped_keys") or [],
        "check_unjudged": unjudged,
        "render_artifacts": render_art,
        "catalog": {k: {"label": e["label"], "role": e.get("role"),
                        "quantity": e.get("quantity"), "render": e.get("render"),
                        "unit": e.get("unit"), "source": e.get("source"),
                        "formula": e.get("formula")}
                    for k, e in E.items()},
    }


# ── HTML — 자기완결. 테스트베드가 이 자리를 대체한다 ─────────────────────────

def render_html(d: dict) -> str:
    esc = _html.escape

    def tile(t):
        v = esc(t["value"]) if t["present"] else "—"
        u = esc(t["unit"] or "")
        cls = "" if t["present"] else " empty"
        return (f'<div class="tile{cls}"><div class="tl">{esc(t["label"])}</div>'
                f'<div class="tv">{v}<span class="tu">{u}</span></div></div>')

    def badge(b):
        if not b["active"]:
            return ""
        return (f'<span class="badge {esc(b["severity"])}" title="{esc(b["why"])}">'
                f'{esc(b["label"])} {b["count"]}</span>')

    def rows(items, cols):
        h = "".join(f"<th>{esc(c[1])}</th>" for c in cols)
        b = "".join("<tr>" + "".join(f"<td>{esc(str(i.get(c[0], '') or ''))}</td>"
                                     for c in cols) + "</tr>" for i in items)
        return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>" if items \
            else '<p class="none">없음</p>'

    panels = {
        "document": f'<pre class="doc">{esc(d["doc_md"])}</pre>',
        "evidence": rows(d["refs_used"], [("line", "줄"), ("key", "키"), ("role", "역할"),
                                          ("text", "인용된 값"), ("role_declared", "역할 선언")]),
        "gate": (f'<p class="{"ok" if d["gate"]["pass"] else "bad"}">'
                 f'{"통과" if d["gate"]["pass"] else "반려"} · 참조 {d["gate"]["n_refs"]}건</p>'
                 + rows(d["gate"]["violations"], [("kind", "종류"), ("location", "위치"),
                                                  ("why", "사유")])),
        "exempt": rows(d["exempt_hits"], [("exemption", "예외"), ("line", "줄"),
                                          ("text", "가려진 것"), ("why", "근거")]),
        "unjudged": rows(d["check_unjudged"], [("check", "항목"), ("unit", "단위"),
                                               ("reason", "사유")]),
        "visuals": rows(d["render_artifacts"], [("name", "파일"), ("kind", "형식"),
                                                ("rel", "경로")]),
    }
    tabs = "".join(
        f'<button class="tab{" on" if i == 0 else ""}" data-p="{esc(p["id"])}">'
        f'{esc(p["label"])}</button>' for i, p in enumerate(d["panels"]))
    bodies = "".join(
        f'<section class="panel{" on" if i == 0 else ""}" id="p-{esc(p["id"])}">'
        f'{panels.get(p["source"], panels.get(p["id"], "<p class=none>미구현</p>"))}</section>'
        for i, p in enumerate(d["panels"]))

    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>{esc(d["title"])} — {esc(d["source_name"])}</title>
<style>
:root{{--bg:#fbfbfa;--fg:#1f2328;--mut:#6b7280;--line:#e3e5e8;--ok:#3f7d5a;--bad:#b05252;--warn:#b58a2a}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.6 -apple-system,"Segoe UI","Noto Sans KR",sans-serif;background:var(--bg);color:var(--fg)}}
header{{padding:24px 28px 16px;border-bottom:1px solid var(--line)}}
h1{{margin:0 0 6px;font-size:20px;font-weight:650}}
.meta{{color:var(--mut);font-size:13px}}
.badges{{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}}
.badge{{font-size:12px;padding:2px 8px;border-radius:10px;border:1px solid var(--line);background:#fff;cursor:help}}
.badge.warn{{border-color:var(--warn);color:#7a5c12}}
.badge.info{{color:var(--mut)}}
.tiles{{display:flex;gap:10px;flex-wrap:wrap;padding:18px 28px}}
.tile{{min-width:132px;padding:10px 14px;background:#fff;border:1px solid var(--line);border-radius:8px}}
.tile.empty .tv{{color:var(--mut)}}
.tl{{font-size:12px;color:var(--mut)}}
.tv{{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}}
.tu{{font-size:12px;font-weight:400;color:var(--mut);margin-left:4px}}
nav{{display:flex;gap:2px;padding:0 28px;border-bottom:1px solid var(--line)}}
.tab{{background:none;border:0;border-bottom:2px solid transparent;padding:9px 12px;font:inherit;font-size:14px;color:var(--mut);cursor:pointer}}
.tab.on{{color:var(--fg);border-bottom-color:var(--fg)}}
.panel{{display:none;padding:20px 28px}} .panel.on{{display:block}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:500}}
.doc{{white-space:pre-wrap;font:13px/1.7 ui-monospace,Menlo,Consolas,monospace;background:#fff;border:1px solid var(--line);border-radius:8px;padding:16px}}
.none{{color:var(--mut)}} .ok{{color:var(--ok)}} .bad{{color:var(--bad)}}
</style>
<header>
  <h1>{esc(d["title"])} — {esc(d["source_name"])}</h1>
  <div class="meta">run {esc(d["run_id"])} · 규칙 {esc(str(d["rule_version"]))} ·
       양식 {esc(str(d["spec_version"]))} · 카탈로그 {d["generated_from"]["catalog"]}항목</div>
  <div class="badges">{"".join(badge(b) for b in d["badges"])}</div>
</header>
<div class="tiles">{"".join(tile(t) for t in d["tiles"])}</div>
<nav>{tabs}</nav>
{bodies}
<script>
document.querySelectorAll('.tab').forEach(function(b){{
  b.addEventListener('click', function(){{
    document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('on')}});
    document.querySelectorAll('.panel').forEach(function(x){{x.classList.remove('on')}});
    b.classList.add('on');
    var p = document.getElementById('p-' + b.dataset.p);
    if (p) p.classList.add('on');
  }});
}});
</script>
</html>"""


RESULT_DIR = "result"          # 산출 폴더 안의 그림 자리 — 문서가 상대 경로로 가리킨다


def _stage_images(work: Path, out: Path, doc_md: str) -> tuple[str, list[str]]:
    """그림을 산출 폴더의 `result/` 로 옮기고 문서의 경로를 **상대 경로로** 다시 쓴다.

    왜 필요한가 — 치환본의 이미지 경로는 `figures/xxx.png` 처럼 **작업 폴더 기준**이다.
    산출 폴더로 문서만 옮기면 그림이 전부 깨진다. 문서를 남에게 보낼 때 그림이 안 따라가면
    시각 근거가 있다는 말이 무의미해진다(I-K).

    ★ 옮기는 것이 아니라 **복사**한다. 작업 폴더는 재현의 근거이므로 비우지 않는다.
    """
    import re
    import shutil
    from urllib.parse import unquote
    rdir = out / RESULT_DIR
    copied: list[str] = []

    def sub(m):
        alt, path = m.group(1), m.group(2)
        if path.startswith(("http://", "https://", f"{RESULT_DIR}/")):
            return m.group(0)
        src = work / unquote(path)
        if not src.is_file():
            return m.group(0)                     # 없는 그림은 손대지 않는다 — 게이트가 잡는다
        rdir.mkdir(parents=True, exist_ok=True)
        dst = rdir / src.name.replace(" ", "_")   # 공백은 마크다운 링크를 끊는다(F-29)
        shutil.copy2(src, dst)
        rel = f"{RESULT_DIR}/{dst.name}"
        copied.append(rel)
        return f"![{alt}]({rel})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", sub, doc_md), sorted(set(copied))


def build(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    d = build_dossier(run_id, work)
    out = C.out_dir(d["source_name"])
    doc, imgs = _stage_images(work, out, d["doc_md"])
    d["doc_md"], d["result_images"] = doc, imgs
    (out / DOC_NAME).write_text(d["doc_md"], encoding="utf-8")
    (out / DOSSIER_NAME).write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (out / HTML_NAME).write_text(render_html(d), encoding="utf-8")
    return {"out_dir": str(out), "doc_path": str(out / DOC_NAME),
            "dossier_path": str(out / DOSSIER_NAME), "html_path": str(out / HTML_NAME),
            "n_tiles": len(d["tiles"]), "n_refs": d["gate"]["n_refs"],
            "n_images": len(imgs), "images": imgs,
            "badges_active": [b["id"] for b in d["badges"] if b["active"]]}


def self_test(run_id: str = "demo-test2") -> int:
    ok = fail = 0

    def chk(n, cond, dt=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {n}")
        else:
            fail += 1
            print(f"  FAIL  {n}  {dt}")

    print(f"[package.py 자기 시험 — {run_id}]")
    try:
        r = build(run_id)
    except Exception as exc:
        print(f"  건너뜀 — {exc}")
        return 2
    d = json.loads(Path(r["dossier_path"]).read_text(encoding="utf-8"))
    chk("dossier 계약 판본", d["dossier_version"] == DOSSIER_VERSION)
    chk("필수 키 전부", {"tiles", "panels", "badges", "doc_md", "gate", "refs_used",
                     "role_undeclared", "exempt_hits", "check_unjudged",
                     "render_artifacts", "catalog"} <= set(d))
    chk(f"타일 {len(d['tiles'])}개", len(d["tiles"]) >= 4)
    chk("게이트 통과본만", d["gate"]["pass"] is True)
    chk("HTML 자기완결(외부 참조 없음)",
        "http://" not in Path(r["html_path"]).read_text(encoding="utf-8").replace(
            "http://www.w3.org", ""))
    chk("md 산출", Path(r["doc_path"]).exists())
    chk("배지 계산", all("count" in b for b in d["badges"]))
    chk("역할 미선언 집계 = refs 중 미선언 수",
        len(d["role_undeclared"]) == sum(1 for x in d["refs_used"]
                                         if not x.get("role_declared")))
    try:
        build_dossier("g4-limit")
        chk("미통과본 포장 거부", False, "통과해 버렸다")
    except Exception:
        chk("미통과본 포장 거부", True)
    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test(*argv[2:3])
    if argv[1] == "build":
        r = build(argv[2])
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"알 수 없는 명령: {argv[1]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
