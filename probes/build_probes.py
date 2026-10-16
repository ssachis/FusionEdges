"""Probe pairs: each one plants a single hard conflict and checks whether the
FusionEdges analyzer reports it. Run: python3 -I build_probes.py <fusionedges-root> <out-dir>"""
import json, sys
from pathlib import Path

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(ROOT))
from fusionedges.analyzer import analyze  # noqa: E402

VITE_PKG = json.dumps({"name": "h", "scripts": {"build": "vite build"},
                       "dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0"},
                       "devDependencies": {"vite": "^5.0.0"}})
HTML = lambda body, head="": f"<!doctype html><html><head>{head}<link rel=\"stylesheet\" href=\"style.css\"></head><body>{body}</body></html>"
HOST_APP = 'import "./index.css";\nexport default function App(){return (<main><h1 className="title">Host</h1></main>);}\n'

def pkg(extra=None, dev=None):
    p = json.loads(VITE_PKG)
    p["dependencies"].update(extra or {})
    p["devDependencies"].update(dev or {})
    return json.dumps(p)

# name: (description, host files, donor files, section, predicate(report)->bool, what a catch looks like)
P = {}

P["behavior-js"] = ("Donor navbar's hamburger menu is driven by a separate js/menu.js (querySelector + classList.toggle). Moving only markup gives a dead button.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ".title{font-size:2rem}"},
    {"index.html": HTML('<nav class="nav"><button class="burger" aria-expanded="false">Menu</button><ul class="menu"><li><a href="#a">A</a></li></ul></nav><script src="js/menu.js"></script>'),
     "style.css": ".menu{display:none}.menu.open{display:block}",
     "js/menu.js": "document.querySelector('.burger').addEventListener('click',()=>document.querySelector('.menu').classList.toggle('open'));"},
    "nav", lambda r: "menu.js" in json.dumps(r) or any("behavio" in f["title"].lower() or "script" in json.dumps(f["evidence"]).lower() for f in r["findings"]),
    "a finding that the section's behavior lives in js/menu.js")

P["router-links"] = ("Host uses react-router; donor uses plain <a href=\"/about\">. Pasted anchors cause full page reloads / 404 on static hosts.",
    {"package.json": pkg({"react-router-dom": "^6.22.0"}), "src/App.jsx": 'import {BrowserRouter,Routes,Route,Link} from "react-router-dom";\nexport default function App(){return (<BrowserRouter><Link to="/">Home</Link><Routes><Route path="/" element={<p/>}/></Routes></BrowserRouter>);}\n', "src/index.css": ""},
    {"index.html": HTML('<footer class="ft"><a href="/about.html">About</a><a href="/blog/index.html">Blog</a></footer>'), "style.css": ".ft{padding:1rem}"},
    "footer", lambda r: any("rout" in (f["title"] + f["detail"]).lower() for f in r["findings"]),
    "a routing finding (anchors vs client-side Link)")

P["specificity"] = ("Host has `#root a { color: black !important }` and `main section h2` rules. Donor classes are unique, so no collision, but host rules still win the cascade.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": "#root a{color:#000 !important;text-decoration:none}\nmain section h2{font-size:12px}"},
    {"index.html": HTML('<section class="promo"><h2 class="promo-title">Big</h2><a class="promo-link" href="#x">Go</a></section>'), "style.css": ".promo-title{font-size:48px}.promo-link{color:#e11}"},
    "promo", lambda r: any("specific" in (f["title"] + f["detail"]).lower() or "#root" in json.dumps(f["evidence"]) for f in r["findings"]),
    "a finding that host descendant/ID rules will override donor styles")

P["inherited-context"] = ("Donor section looks right only because of an ancestor: `.theme-dark` on <body> sets color/background, and the section is white text.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": "body{background:#fff;color:#111}"},
    {"index.html": '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head><body class="theme-dark"><section class="stats"><p class="stat">99%</p></section></body></html>',
     "style.css": ".theme-dark{background:#0b0b10;color:#fafafa}.stat{font-weight:700}"},
    "stats", lambda r: "theme-dark" in json.dumps(r["findings"]),
    "a finding that the section depends on ancestor class .theme-dark")

P["css-url-assets"] = ("Donor background image and @font-face fonts referenced via url() inside CSS, not in markup.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ""},
    {"index.html": HTML('<section class="hero"><h1 class="hero-h">Hi</h1></section>'),
     "style.css": "@font-face{font-family:Brand;src:url(fonts/brand.woff2)}.hero{background:url(../img/hero.jpg) center/cover}.hero-h{font-family:Brand}"},
    "hero", lambda r: any("hero.jpg" in json.dumps(f["evidence"]) or "brand.woff2" in json.dumps(f["evidence"]) for f in r["findings"]),
    "an asset finding listing img/hero.jpg and fonts/brand.woff2")

P["icon-font-cdn"] = ("Donor footer uses Font Awesome classes (fa fa-twitter) loaded from a CDN in <head>. Host has no icon font, so icons render as empty boxes.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ""},
    {"index.html": HTML('<footer class="ft"><i class="fa fa-twitter"></i><i class="fa fa-github"></i></footer>', '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">'),
     "style.css": ".ft{padding:1rem}"},
    "footer", lambda r: any(f["severity"] in ("medium", "high") and ("fa" in json.dumps(f["evidence"]) or "icon" in f["title"].lower()) for f in r["findings"]),
    "a medium/high finding that classes fa, fa-twitter have no definition in donor CSS and come from a CDN")

P["unstyled-classes"] = ("Donor classes defined in a second stylesheet the page links later (components.css); analyzer only reads files it can resolve.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ".card{border:1px solid red}"},
    {"index.html": '<!doctype html><html><head><link rel="stylesheet" href="/assets/css/components.css"></head><body><section class="pricing"><div class="card">x</div></section></body></html>',
     "assets/css/components.css": ".card{border-radius:12px;box-shadow:0 2px 8px #0002}"},
    "pricing", lambda r: any(f["category"] == "css-class-collision" for f in r["findings"]),
    "a css-class-collision on .card (root-absolute stylesheet href)")

P["scss-donor"] = ("Donor styles are SCSS (nested, $vars, mixins). Host has no sass dependency.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ".btn{color:red}"},
    {"package.json": pkg({}, {"sass": "^1.70.0"}), "src/components/Banner.jsx": 'import "./Banner.scss";\nexport default function Banner(){return <div className="banner"><button className="btn">Go</button></div>;}\n',
     "src/components/Banner.scss": "$brand:#0af;\n.banner{ .btn{ background:$brand; } }"},
    "banner", lambda r: any("sass" in json.dumps(f).lower() or "scss" in json.dumps(f).lower() for f in r["findings"]),
    "a finding that donor styles are SCSS and host lacks sass")

P["css-modules"] = ("Donor React component uses CSS Modules (className={styles.card}). Analyzer's class regex only sees string literals.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ".card{color:red}"},
    {"package.json": pkg(), "src/components/Card.jsx": 'import styles from "./Card.module.css";\nexport default function Card(){return <div className={styles.card}>x</div>;}\n',
     "src/components/Card.module.css": ".card{padding:2rem}"},
    "card", lambda r: len(r.get("donor_section", {}).get("classes", [])) > 0 or any("module" in json.dumps(f).lower() for f in r["findings"]),
    "classes detected from styles.card, or a CSS-modules finding")

P["react-to-html-state"] = ("React donor with useState, props and fetch() for data. HTML host needs vanilla JS and static data.",
    {"index.html": HTML("<main><h1>Host</h1></main>"), "style.css": "h1{color:#111}"},
    {"package.json": pkg(), "src/components/Testimonials.jsx": 'import {useState,useEffect} from "react";\nexport default function Testimonials({limit}){const [items,set]=useState([]);useEffect(()=>{fetch("/api/t").then(r=>r.json()).then(set)},[]);return <section className="t">{items.slice(0,limit).map(i=><p key={i.id}>{i.text}</p>)}</section>;}\n'},
    "testimonials", lambda r: any("fetch" in json.dumps(f).lower() or "api" in json.dumps(f).lower() for f in r["findings"]),
    "a finding that data comes from fetch('/api/t'), which the host does not serve")

P["tailwind-config"] = ("Both Tailwind, but donor uses custom theme tokens (bg-brand-500, font-display) from its tailwind.config.js, absent in host config.",
    {"package.json": pkg({}, {"tailwindcss": "^3.4.0"}), "tailwind.config.js": "module.exports={content:['./src/**/*.jsx'],theme:{extend:{}}}", "src/App.jsx": HOST_APP, "src/index.css": "@tailwind base;@tailwind utilities;"},
    {"package.json": pkg({}, {"tailwindcss": "^3.4.0"}), "tailwind.config.js": "module.exports={theme:{extend:{colors:{brand:{500:'#7c3aed'}},fontFamily:{display:['Clash']}}}}",
     "src/components/Cta.jsx": 'export default function Cta(){return <div className="bg-brand-500 font-display p-8">Join</div>;}\n'},
    "cta", lambda r: "brand" in json.dumps(r["findings"]),
    "a finding that bg-brand-500 / font-display depend on donor tailwind config")

P["tailwind-v3-v4"] = ("Donor Tailwind v3 (tailwind.config.js, @tailwind directives), host Tailwind v4 (@import \"tailwindcss\", CSS-first config).",
    {"package.json": pkg({}, {"tailwindcss": "^4.0.0", "@tailwindcss/vite": "^4.0.0"}), "src/App.jsx": HOST_APP, "src/index.css": '@import "tailwindcss";'},
    {"package.json": pkg({}, {"tailwindcss": "^3.4.0"}), "tailwind.config.js": "module.exports={}",
     "src/components/Hero.jsx": 'import "./hero.css";\nexport default function Hero(){return <div className="shadow-sm rounded ring">Hi</div>;}\n', "src/components/hero.css": "@tailwind components;"},
    "hero", lambda r: any(f["category"] == "dependency-version-conflict" or "v4" in json.dumps(f) for f in r["findings"]),
    "a version finding on tailwindcss 3 vs 4 (renamed utilities like shadow-sm, ring)")

P["fixed-overlay"] = ("Donor navbar is position:fixed with z-index:9999 and the donor body has padding-top:80px to compensate; host has its own sticky header.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": ".site-head{position:sticky;top:0;z-index:10}"},
    {"index.html": HTML('<nav class="topbar"><a href="#">Logo</a></nav>'), "style.css": "body{padding-top:80px}.topbar{position:fixed;top:0;left:0;right:0;height:80px;z-index:9999}"},
    "nav", lambda r: any("fixed" in json.dumps(f).lower() or "z-index" in json.dumps(f).lower() for f in r["findings"]),
    "a layout finding: fixed positioning + z-index will overlay host content")

P["breakpoints"] = ("Donor collapses at 768px via media query, host layout system breaks at 1024px; mobile nav only exists in donor CSS @media block.",
    {"package.json": pkg(), "src/App.jsx": HOST_APP, "src/index.css": "@media (max-width:1024px){.title{font-size:1rem}}"},
    {"index.html": HTML('<nav class="nav"><ul class="links"><li>A</li></ul></nav>'), "style.css": ".links{display:flex}@media (max-width:768px){.links{display:none}}"},
    "nav", lambda r: any("breakpoint" in json.dumps(f).lower() or "@media" in json.dumps(f).lower() for f in r["findings"]),
    "a responsive finding: donor rules inside @media were seen, breakpoints differ")

P["dup-id-in-css-js"] = ("Donor id `#signup` is styled in CSS and used by a host anchor `href=\"#signup\"` elsewhere; ids only compared, anchors not.",
    {"package.json": pkg(), "src/App.jsx": 'export default function App(){return <main><a href="#signup">Join</a><form id="signup"></form></main>;}\n', "src/index.css": ""},
    {"index.html": HTML('<section class="news"><form id="signup"><input/></form></section>'), "style.css": "#signup{display:grid}"},
    "news", lambda r: any(f["category"] == "id-collision" for f in r["findings"]),
    "id-collision on signup (control: should already be caught)")


def write(base: Path, files: dict):
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


rows = []
for name, (desc, host, donor, section, pred, want) in P.items():
    d = OUT / name
    write(d / "host", host)
    write(d / "donor", donor)
    r = analyze(d / "host", d / "donor", section)
    found = r.get("donor_section", {}).get("found", False)
    caught = bool(found and pred(r))
    rows.append({"probe": name, "section_found": found, "caught": caught,
                 "findings": [f"{f['severity']}:{f['category']}" for f in r["findings"]],
                 "description": desc, "expected": want})
    (d / "report.json").write_text(json.dumps(r, indent=1))

(OUT / "results.json").write_text(json.dumps(rows, indent=1))
for x in rows:
    flag = "CAUGHT" if x["caught"] else ("NO-SECTION" if not x["section_found"] else "MISSED")
    print(f"{flag:10} {x['probe']:22} {', '.join(x['findings']) or '-'}")
