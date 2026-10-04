"""The printed recipe book. Every page carries a QR code that plays the cook telling it."""
import html
import io

import qrcode
import qrcode.image.svg

from . import config, store


def _qr_svg(url: str) -> str:
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=1)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return svg[svg.find("<svg"):]


def recipes() -> list[dict]:
    out = []
    for m in store.db().memos.find({"status": "ready", "recipe": {"$ne": None}}, {"words": 0, "segments": 0}):
        out.append({"memo_id": str(m["_id"]), "title": m["title"], "recipe": m["recipe"],
                    "quotes": m.get("quotes", []), "summary": m.get("summary", ""),
                    "duration": m.get("duration"), "audio_id": str(m["audio_id"])})
    out.sort(key=lambda r: r["recipe"]["name"].lower())
    return out


def render() -> str:
    fam = store.get_family()
    elder = html.escape(fam.get("elder_name") or "Our cook")
    family = html.escape(fam.get("family_name") or "the people who love them")
    pr = store.pronouns(fam)
    items = recipes()
    e = html.escape
    pages = []
    for r in items:
        rec = r["recipe"]
        listen = f"{config.PUBLIC_BASE_URL}/#memo/{r['memo_id']}"
        quote = f'<blockquote>&ldquo;{e(r["quotes"][0])}&rdquo;</blockquote>' if r["quotes"] else ""
        serves = f'<p class="serves">Serves {e(rec["serves"])}</p>' if rec.get("serves") else ""
        tips = (f"<h3>The way {pr['sub']} does it</h3><ul>" + "".join(f"<li>{e(t)}</li>" for t in rec["tips"]) + "</ul>") if rec.get("tips") else ""
        pages.append(f"""
<section class="page">
  <h2>{e(rec["name"])}</h2>{serves}{quote}
  <div class="cols">
    <div><h3>You will need</h3><ul>{"".join(f"<li>{e(i)}</li>" for i in rec["ingredients"])}</ul></div>
    <div><h3>Method</h3><ol>{"".join(f"<li>{e(s)}</li>" for s in rec["steps"])}</ol>{tips}</div>
  </div>
  <footer><div class="qr">{_qr_svg(listen)}</div><p>Scan to hear {elder} tell it in {pr['pos']} own voice.<br><small>From the recording &ldquo;{e(r["title"])}&rdquo;</small></p></footer>
</section>""")
    if not pages:
        pages.append(f'<section class="page empty"><p>No recipes yet. Record {elder} explaining a dish and it will appear here.</p></section>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{elder}'s Kitchen</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Source+Serif+4:wght@400;600&display=swap" rel="stylesheet">
<style>
:root {{ --ink:#2b2118; --paper:#fbf6ec; --accent:#9a4a22; --rule:#e2d5bf; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#e9e1d2; color:var(--ink); font-family:"Source Serif 4", Georgia, serif; line-height:1.55; }}
.toolbar {{ text-align:center; padding:16px; }}
.toolbar button {{ font:inherit; padding:10px 18px; border:0; border-radius:999px; background:var(--accent); color:#fff; cursor:pointer; }}
.cover, .page {{ background:var(--paper); max-width:780px; margin:16px auto; padding:56px 64px; box-shadow:0 2px 14px rgba(0,0,0,.08); }}
.cover {{ text-align:center; padding:120px 64px; }}
.cover h1 {{ font-family:Fraunces, serif; font-size:56px; margin:0 0 12px; font-weight:600; }}
.cover p {{ font-size:20px; margin:4px 0; }}
h2 {{ font-family:Fraunces, serif; font-size:36px; margin:0 0 6px; color:var(--accent); font-weight:600; }}
h3 {{ font-family:Fraunces, serif; font-size:18px; margin:18px 0 6px; }}
.serves {{ margin:0; font-style:italic; }}
blockquote {{ margin:18px 0; padding:4px 0 4px 18px; border-left:3px solid var(--accent); font-style:italic; font-size:19px; }}
.cols {{ display:grid; grid-template-columns:1fr 1.6fr; gap:32px; }}
li {{ margin:4px 0; }}
footer {{ display:flex; gap:16px; align-items:center; border-top:1px solid var(--rule); margin-top:28px; padding-top:16px; }}
.qr svg {{ width:84px; height:84px; display:block; }}
.empty {{ text-align:center; font-style:italic; }}
@media (max-width:700px) {{ .cover, .page {{ padding:32px 20px; margin:12px 0; }} .cols {{ grid-template-columns:1fr; }} .cover h1 {{ font-size:40px; }} }}
@media print {{ body {{ background:#fff; }} .toolbar {{ display:none; }} .cover, .page {{ box-shadow:none; margin:0; max-width:none; page-break-after:always; }} }}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">Print or save as PDF</button></div>
<section class="cover"><h1>{elder}'s Kitchen</h1><p>Recipes in {pr['pos']} own words</p><p>Kept by {family}</p><p><small>{len(items)} recipe{"s" if len(items) != 1 else ""}</small></p></section>
{"".join(pages)}
</body></html>"""
