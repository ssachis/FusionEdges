import Link from "next/link";

export default function Header() {
  return (
    <header className="border-b px-6 py-4">
      <Link href="/" className="font-semibold">Fieldnotes</Link>
    </header>
  );
}
