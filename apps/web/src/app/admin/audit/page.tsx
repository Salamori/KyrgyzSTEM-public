import { AdminShell } from "@/components/admin/AdminShell";
import { AuditLog } from "@/components/admin/AuditLog";

export default function AdminAuditPage() {
  return (
    <AdminShell>
      <AuditLog />
    </AdminShell>
  );
}
