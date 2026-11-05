// Dutch 404 inside the app shell (sidebar/topbar stay), for unknown URLs (via [...missing]) and notFound() calls.
import { NotFoundView } from "@/components/shell/not-found-view";

export default function AppNotFound() {
  return <div className="card" style={{ marginTop: 16 }}><NotFoundView /></div>;
}
