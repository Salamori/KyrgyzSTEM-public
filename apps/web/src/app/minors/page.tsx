import { LegalPage } from "@/components/LegalPage";

export default function MinorsPage() {
  return (
    <LegalPage
      doc="minors"
      sections={[
        { id: "who", needsDecision: true },
        { id: "data" },
        { id: "school", needsDecision: true },
        { id: "guardians", needsDecision: true },
      ]}
    />
  );
}
