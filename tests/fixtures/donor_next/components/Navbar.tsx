import Link from "next/link";

export default function Navbar() {
  return (
    <header className="border-b border-gray-200">
      <nav className="mx-auto flex max-w-5xl gap-6 px-6 py-4 text-sm font-medium">
        <Link href="/" className="text-brand-700">Acme</Link>
        <Link href="/blog" className="hover:text-brand-500">Blog</Link>
      </nav>
    </header>
  );
}
