import "./globals.css";
import Navbar from "@/components/Navbar";

export const metadata = { title: "Acme Studio", description: "Design studio" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-display antialiased">
        <Navbar />
        {children}
      </body>
    </html>
  );
}
