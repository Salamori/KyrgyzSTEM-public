import { AdminShell } from "@/components/admin/AdminShell";
import { ContentQueue } from "@/components/admin/ContentQueue";

export default function AdminProblemsPage() {
  return (
    <AdminShell>
      <ContentQueue />
    </AdminShell>
  );
}
