import { LegalPage } from "@/components/LegalPage";

export default function TermsPage() {
  return (
    <LegalPage
      doc="terms"
      sections={[
        { id: "service", needsDecision: true },
        { id: "account" },
        { id: "conduct" },
        { id: "content", needsDecision: true },
        { id: "limits", needsDecision: true },
        { id: "ending" },
        { id: "changes", needsDecision: true },
      ]}
    />
  );
}
