import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { Providers } from "@/components/Providers";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";
import { DEFAULT_LOCALE, LOCALE_COOKIE, isLocale, translate } from "@/lib/i18n";

import "./globals.css";

export const metadata: Metadata = {
  title: "KyrgyzSTEM",
  description: "Olympiad preparation for high-school students in Kyrgyzstan.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(cookieLocale) ? cookieLocale : DEFAULT_LOCALE;

  return (
    <html lang={locale}>
      <body className="flex min-h-screen flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:text-slate-900 focus:p-2"
        >
          {translate(locale, "app.skipToContent")}
        </a>
        <Providers locale={locale}>
          <SiteHeader />
          <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-14">
            {children}
          </main>
          <SiteFooter />
        </Providers>
      </body>
    </html>
  );
}
