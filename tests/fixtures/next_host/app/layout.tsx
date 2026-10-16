import "./globals.css";
import Header from "@/components/Header";

export const metadata = { title: "Fieldnotes", description: "A small notes app" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-white text-slate-900">
        <Header />
        {children}
      </body>
    </html>
  );
}
