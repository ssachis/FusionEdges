import Link from 'next/link';
import { NavbarTwoColumns } from '../navigation/NavbarTwoColumns';
import { CenteredFooter } from '../footer/CenteredFooter';
export default function Reference() { return <main style={{maxWidth:960,margin:'32px auto',padding:16}}>
<h1>Donor reference fixtures</h1>
<section data-fe-transfer="pilot-navbar"><NavbarTwoColumns logo={<strong>Fusion Pilot</strong>}><li><Link href="/">Home</Link></li><li><Link href="https://example.com/docs">Documentation</Link></li></NavbarTwoColumns></section>
<div style={{height:160}}>Reference spacing — not transferred.</div>
<footer data-fe-transfer="pilot-footer"><CenteredFooter logo={<strong>Fusion Pilot</strong>} iconList={null}><li><Link href="/">Home</Link></li><li><Link href="https://example.com/privacy">Privacy</Link></li></CenteredFooter></footer>
</main> };
