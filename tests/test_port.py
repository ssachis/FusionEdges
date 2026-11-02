"""Condition M / MA: the deterministic port (no AI) and the AI layer that only runs when the port fails."""
import json
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fusionedges import compare, llm, paths, port, store
from fusionedges.pipeline import run, scan_for
from fusionedges.prompts import default_prompt
from tests import fake_llm

FIX = Path(__file__).resolve().parent / "fixtures"
HOST, DONOR = FIX / "host_react", FIX / "donor_next"


def identity_strip(files, log=print):
    """Stand-in for the TypeScript compiler (not installed in every test environment): the fixtures have no types."""
    return dict(files)


class PortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        shutil.copytree(HOST, self.out)
        self.p = [unittest.mock.patch.object(port, "strip_types", identity_strip),
                  unittest.mock.patch.object(port, "compile_tailwind", lambda *a, **k: {"ok": False, "reason": "offline"})]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _port(self, host=HOST, donor=DONOR, out=None):
        sc = scan_for(host, donor, False, "navbar")
        return port.port(host, donor, out or self.out, sc, "navbar", log=lambda m: None), sc

    def test_inline_header_replaced_and_next_shimmed(self):
        r, _ = self._port()
        self.assertTrue(r["ok"])
        nav = (self.out / "src/fusion/navbar/components/Navbar.jsx").read_text()
        self.assertIn('import Link from "../next-shims"', nav)
        self.assertNotIn("next/link", nav)
        shims = (self.out / "src/fusion/navbar/next-shims.jsx").read_text()
        self.assertIn("export default function Link", shims)
        self.assertNotIn("react-router-dom", shims)      # the host has no router: plain <a> links
        app = (self.out / "src/App.jsx").read_text()
        self.assertIn('import FusionNavbar from "./fusion/navbar"', app)
        self.assertIn("<FusionNavbar />", app)
        self.assertNotIn('<header className="nav">', app)  # replaced, not duplicated
        self.assertEqual(r["mount"]["how"], "replaced inline <header>")
        kinds = {x["kind"] for x in r["resolved"]}
        self.assertTrue({"framework-api", "routing-model", "mount", "language"} <= kinds | {"language"})
        self.assertIn("styling-system", {u["kind"] for u in r["unresolved"]})   # Tailwind offline -> left for AI
        entry = (self.out / "src/fusion/navbar/index.jsx").read_text()
        self.assertIn('className="fe-navbar"', entry)
        self.assertTrue((HOST / "src/App.jsx").read_text().count('<header className="nav">') == 1)  # original untouched

    def test_existing_component_imports_repointed(self):
        host = self.tmp / "host2"
        shutil.copytree(HOST, host)
        (host / "src/components/Header.jsx").write_text(
            'import { Link } from "react-router-dom";\nexport function Header() { return <nav className="navbar"><Link to="/">Home</Link></nav>; }\n')
        (host / "src/App.jsx").write_text(
            'import { Header as Top } from "./components/Header";\nexport default function App() { return (<div><Top /></div>); }\n')
        (host / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0",
                                                                          "react-router-dom": "^6.20.0"}}))
        out = self.tmp / "out2"
        shutil.copytree(host, out)
        r, _ = self._port(host=host, out=out)
        self.assertEqual(r["mount"]["how"], "replaced")
        self.assertIn('import Top from "./fusion/navbar"', (out / "src/App.jsx").read_text())
        self.assertIn("useNavigate", (out / "src/fusion/navbar/next-shims.jsx").read_text())  # react-router 6

    def test_shim_clauses(self):
        donor = self.tmp / "d"
        shutil.copytree(DONOR, donor)
        (donor / "components/Navbar.tsx").write_text(
            'import Image from "next/image";\nimport { Inter } from "next/font/google";\n'
            'import { usePathname } from "next/navigation";\nimport { motion } from "framer-motion";\n'
            'const inter = Inter({ subsets: ["latin"] });\n'
            'export default function Navbar() { const p = usePathname(); return <motion.nav className={inter.className}>'
            '<Image src="/logo.png" alt="" width={20} height={20} />{p}</motion.nav>; }\n')
        (donor / "public").mkdir(exist_ok=True)
        (donor / "public/logo.png").write_bytes(b"\x89PNG....")
        (donor / "package.json").write_text(json.dumps({"dependencies": {"next": "15.0.0", "react": "19.0.0",
                                                                           "framer-motion": "^11.0.0"}}))
        r, _ = self._port(donor=donor)
        nav = (self.out / "src/fusion/navbar/components/Navbar.jsx").read_text()
        self.assertIn('import { Image as Image } from "../next-shims"', nav)
        self.assertIn('import { font as Inter } from "../next-shims"', nav)
        self.assertIn('import { usePathname } from "../next-shims"', nav)
        self.assertEqual(r["packages_added"], {"framer-motion": "^11.0.0"})
        self.assertIn("framer-motion", json.loads((self.out / "package.json").read_text())["dependencies"])
        self.assertTrue((self.out / "public/logo.png").exists())

    def test_theme_colors_from_donor_css(self):
        css, _, colors = port.donor_theme_css(DONOR, ".fe-navbar")
        self.assertIn(".fe-navbar {", css)
        self.assertIn("--background: #ffffff", css)
        self.assertEqual(colors["background"], "var(--background)")

    def test_fair_model_rule(self):
        self.assertTrue(compare._same_model(["g:x"], [], "port"))            # M used no model
        self.assertTrue(compare._same_model(["g:x"], ["g:x"], "port-ai"))
        self.assertFalse(compare._same_model(["g:x"], ["g:y"], "port-ai"))
        self.assertFalse(compare._same_model(["g:x"], [], "report"))         # a report run with no model is broken


class PortPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = (paths.HOME, paths.EXAMPLES, paths.OUTPUT, paths.DB)
        paths.HOME, paths.EXAMPLES, paths.OUTPUT = self.tmp, self.tmp / "examples", self.tmp / "output"
        paths.DB = paths.OUTPUT / "runs.db"
        self.p = [unittest.mock.patch.object(port, "strip_types", identity_strip),
                  unittest.mock.patch.object(port, "compile_tailwind", lambda *a, **k: {"ok": False, "reason": "offline"})]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        paths.HOME, paths.EXAMPLES, paths.OUTPUT, paths.DB = self._old
        llm.CUSTOM.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, mode):
        return run({"mode": mode, "source": str(HOST), "donor": str(DONOR), "add_component": True, "component": "navbar",
                    "prompt": default_prompt(False, False, "navbar"), "build": False, "browser": False}, lambda m: None)

    def test_port_mode_uses_no_tokens(self):
        rec = self._run("port")
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["agent"]["api_calls"], 0)
        self.assertEqual(rec["models"], [])
        self.assertTrue((Path(rec["folder"]) / "port.json").exists())
        self.assertTrue(rec["checks"]["checks"]["mounted"]["pass"] if "mounted" in rec["checks"]["checks"] else True)

    def test_port_ai_calls_model_only_for_what_is_left(self):
        calls = []

        def fake(system, messages, tools, max_tokens):
            calls.append(messages[0]["text"])
            css = '<<<FILE src/fusion/navbar/fusion.css>>>\n.fe-navbar nav{display:flex}\n<<<END>>>\n<<<DONE>>>\nadded css'
            return {"text": css, "tool_calls": []}, {"in": 1000, "out": 50}
        fake_llm.install(("fake", fake))
        rec = self._run("port-ai")
        # Tailwind could not be compiled offline -> styling-system is open -> exactly one small AI call
        self.assertEqual(rec["agent"]["api_calls"], 1)
        self.assertIn("ALREADY DONE", calls[0])
        self.assertIn("styling-system", calls[0])
        self.assertLess(len(calls[0]), 20000)

    def test_pairs_with_baseline_as_condition_m(self):
        fake_llm.install(("second", fake_llm.smart))
        base = self._run("prompt")
        m = self._run("port")
        with store.connect() as db:
            row = db.execute("SELECT condition, prompt_run, fair FROM comparisons WHERE report_run=?", (m["id"],)).fetchone()
        self.assertEqual(row[0], "M")
        self.assertEqual(row[1], base["id"])
        self.assertEqual(row[2], 1)


if __name__ == "__main__":
    unittest.main()


class PortV2Test(unittest.TestCase):
    """Fixes from the first 20 real M runs (1 Nov): barrels, backend features, versions, old router, CSS-first finds."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = [unittest.mock.patch.object(port, "strip_types", identity_strip),
                  unittest.mock.patch.object(port, "compile_tailwind", lambda *a, **k: {"ok": False, "reason": "offline"}),
                  unittest.mock.patch.object(port, "_packument", lambda n, timeout=15: (_ for _ in ()).throw(OSError("offline")))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _donor(self, files: dict, deps=None):
        d = self.tmp / "donor"
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        (d / "package.json").write_text(json.dumps({"dependencies": {"next": "15.0.0", "react": "19.0.0", **(deps or {})}}))
        (d / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"paths": {"@/*": ["./*"]}}}))
        return d

    def _run(self, donor, host=HOST):
        out = self.tmp / "out"
        shutil.copytree(host, out)
        r = port.port(host, donor, out, scan_for(host, donor, False, "navbar"), "navbar", log=lambda m: None)
        return r, out

    def test_barrel_import_is_split_per_name(self):
        d = self._donor({
            "components/Navbar.jsx": 'import { NAV, NAME } from "@/constant";\nexport default function Navbar() { return <nav>{NAME}{NAV.length}</nav>; }\n',
            "constant/index.js": 'export * from "./nav";\nexport * from "./person";\nexport * from "./seo";\n',
            "constant/nav.js": 'export const NAV = [1, 2];\n',
            "constant/person.js": 'export const NAME = "x";\n',
            "constant/seo.js": 'import big from "./huge";\nexport const SEO = big;\n',
            "constant/huge.js": 'export default 1;\n'})
        r, out = self._run(d)
        nav = (out / "src/fusion/navbar/components/Navbar.jsx").read_text()
        self.assertIn('import { NAV } from "../constant/nav"', nav)
        self.assertIn('import { NAME } from "../constant/person"', nav)
        self.assertFalse((out / "src/fusion/navbar/constant/seo.js").exists())   # not needed, not copied
        self.assertFalse(any(u["kind"] == "missing-import" for u in r["unresolved"]))

    def test_backend_component_becomes_stand_in(self):
        d = self._donor({
            "components/Navbar.jsx": 'import { SignIn } from "./SignIn";\nexport default function Navbar() { return <nav><a href="/">Home</a><SignIn /></nav>; }\n',
            "components/SignIn.jsx": 'import { auth } from "@/lib/auth";\nexport function SignIn() { const s = auth.useSession(); return <button>{s.data.user}</button>; }\n',
            "lib/auth.js": 'import { createAuthClient } from "better-auth/react";\nexport const auth = createAuthClient({});\n'},
            {"better-auth": "^1.0.0"})
        r, out = self._run(d)
        signin = (out / "src/fusion/navbar/components/SignIn.jsx").read_text()
        self.assertIn("export function SignIn() { return null; }", signin)
        self.assertNotIn("better-auth", json.loads((out / "package.json").read_text()).get("dependencies", {}))
        self.assertFalse((out / "src/fusion/navbar/lib/auth.js").exists())
        self.assertIn("Home", (out / "src/fusion/navbar/components/Navbar.jsx").read_text())   # the navbar itself stays

    def test_mixed_data_module_kept_with_leaf_stubs(self):
        d = self._donor({
            "components/Navbar.jsx": 'import { mainNav } from "@/constant/navigation";\nexport default function Navbar() { return <nav>{mainNav.map(n => <a key={n}>{n}</a>)}</nav>; }\n',
            "constant/navigation.js": 'import { createClient } from "next-sanity";\nconst client = createClient({});\n'
                                      'export const mainNav = ["Home", "Blog"];\nexport async function getBlogs() { return client.fetch("q"); }\n'})
        r, out = self._run(d)
        nav = (out / "src/fusion/navbar/constant/navigation.js").read_text()
        self.assertIn('export const mainNav = ["Home", "Blog"]', nav)
        self.assertNotIn("next-sanity", nav)
        self.assertIn("createClient", (out / "src/fusion/navbar/backend-stubs.js").read_text())

    def test_server_wrapper_retargets_to_client_component(self):
        d = self._donor({
            "components/Navbar.jsx": 'import NavbarClient from "./NavbarClient";\nimport { getNav } from "@/lib/data";\n'
                                     'export default async function Navbar() { const n = await getNav(); return <NavbarClient items={n} />; }\n',
            "components/NavbarClient.jsx": '"use client";\nexport default function NavbarClient({ items = [] }) { return <nav>{items.length}</nav>; }\n',
            "lib/data.js": 'import { sql } from "@vercel/postgres";\nexport async function getNav() { return sql`x`; }\n'})
        r, _ = self._run(d)
        self.assertTrue(any(x["kind"] == "server-component" for x in r["resolved"]))
        self.assertIn("NavbarClient.jsx", r["entry"] and "".join(r["files"]))

    def test_existing_component_found_through_its_css(self):
        host = self.tmp / "host"
        shutil.copytree(HOST, host)
        (host / "src/components/Navigation").mkdir(parents=True)
        (host / "src/components/Navigation/Navigation.css").write_text(".nav{}")
        (host / "src/components/Navigation/index.js").write_text('import "./Navigation.css";\nexport default function Navigation(){ return <nav/>; }\n')
        self.assertEqual(port.existing_component(host, "navbar"), "src/components/Navigation/index.js")

    def test_versions_fit_site_react(self):
        doc = {"versions": {"2.2.9": {"peerDependencies": {"react": "^18 || ^19"}},
                            "1.7.19": {"peerDependencies": {"react": "^16 || ^17 || ^18"}},
                            "1.7.0": {"peerDependencies": {"react": "^16 || ^17 || ^18"}}}}
        got, notes = port.fit_versions({"@headlessui/react": "2.2.9"}, "17.0.2", fetch=lambda n: doc)
        self.assertEqual(got["@headlessui/react"], "^1.7.19")
        got, _ = port.fit_versions({"@headlessui/react": "^2.0.0"}, "18.3.1", fetch=lambda n: doc)
        self.assertEqual(got["@headlessui/react"], "^2.0.0")          # donor's range already fits: unchanged

    def test_old_router_has_no_hooks(self):
        out = self.tmp / "o"
        out.mkdir()
        (out / "package-lock.json").write_text(json.dumps({"packages": {"node_modules/react-router-dom": {"version": "5.0.1"}}}))
        self.assertFalse(port.router_has_hooks(out))
        src = port.shims_source("react-router-5", hooks=False)
        self.assertNotIn("useLocation", src)
        self.assertIn("RouterLink", src)
