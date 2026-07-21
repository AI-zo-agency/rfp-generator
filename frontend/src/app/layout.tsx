import type { Metadata } from "next";
import { Cabin, Open_Sans } from "next/font/google";
import { ThemeProvider } from "@/components/ThemeProvider";
import "./globals.css";

const openSans = Open_Sans({
  variable: "--font-open-sans",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700", "800"],
  display: "swap",
});

const cabin = Cabin({
  variable: "--font-cabin",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Zo RFP Intelligence Studio | zö agency",
  description:
    "Sync JustWin solicitations, go/no-go review, and RFP tracking for zö agency.",
  icons: {
    icon: "/icon.png",
    apple: "/icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${openSans.variable} ${cabin.variable} h-full antialiased`}
      data-theme="light"
      suppressHydrationWarning
    >
      <head>
        <style
          dangerouslySetInnerHTML={{
            __html: `
              html.auth-route {
                background: #0a0f1a !important;
              }
              html.auth-route body {
                background: #0a0f1a !important;
              }
              html.auth-route body::before {
                display: none !important;
              }
            `,
          }}
        />
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){document.documentElement.setAttribute('data-theme','light');var p=location.pathname;if(p==='/login'||p==='/signup'||p.indexOf('/login/')===0||p.indexOf('/signup/')===0){document.documentElement.classList.add('auth-route');document.documentElement.style.backgroundColor='#0a0f1a';new Image().src='/auth/zo_Grid.webp';new Image().src='/auth/skateboard-bg.webp';}})();`,
          }}
        />
      </head>
      <body className="min-h-full" suppressHydrationWarning>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
