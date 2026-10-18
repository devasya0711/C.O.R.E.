import type { ClerkProvider } from "@clerk/nextjs";
import type { ComponentProps } from "react";

export const clerkAppearance = {
  variables: {
    colorPrimary: "#4F46E5",
    colorPrimaryForeground: "#F3F4F6",
    colorBackground: "#11131C",
    colorForeground: "#F3F4F6",
    colorMuted: "#181B26",
    colorMutedForeground: "#9CA3AF",
    colorInput: "#181B26",
    colorInputForeground: "#F3F4F6",
    colorNeutral: "#F3F4F6",
    colorBorder: "#262938",
    colorRing: "#4F46E5",
    colorDanger: "#EF4444",
    colorSuccess: "#22C55E",
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
    fontFamilyMono: "'JetBrains Mono', ui-monospace, monospace",
    fontSize: "14px",
    borderRadius: "6px",
  },
  elements: {
    rootBox: "w-full",
    cardBox: "w-full rounded-lg border border-hairline shadow-none",
    card: "bg-panel shadow-none",
    headerTitle: "text-2xl font-semibold tracking-[-0.015em] text-primaryText",
    headerSubtitle: "text-sm text-secondaryText",
    formFieldInput: "rounded-md border border-hairline bg-surface text-primaryText",
    formFieldLabel: "text-sm text-primaryText",
    formFieldErrorText: "text-xs text-red-500",
    formButtonPrimary: "rounded-md bg-active text-sm font-semibold text-white shadow-none",
    socialButtonsBlockButton: "rounded-md border border-hairline bg-surface text-primaryText",
    footer: "bg-panel bg-none",
    footerActionText: "text-secondaryText",
    footerActionLink: "text-indigo-300 hover:text-indigo-200",
  },
} satisfies ComponentProps<typeof ClerkProvider>["appearance"];
