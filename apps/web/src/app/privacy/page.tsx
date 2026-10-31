import { LegalPage } from "@/components/LegalPage";

export default function PrivacyPolicyPage() {
  return (
    <LegalPage
      doc="privacy"
      sections={[
        { id: "operator", needsDecision: true },
        { id: "data" },
        { id: "purpose", needsDecision: true },
        { id: "sharing", needsDecision: true },
        { id: "retention", needsDecision: true },
        { id: "rights" },
        { id: "contact", needsDecision: true },
      ]}
    />
  );
}
