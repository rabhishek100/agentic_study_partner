import "./globals.css";

export const metadata = {
  title: "Agentic Study Partner",
  description: "A grounded study companion for technical books",
  icons: {
    icon: "/favicon.svg",
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
