// Unknown URLs render the Dutch 404 inside the app shell instead of Next.js' bare English page.
// Static routes always win over this catch-all; /api/v1 is proxied before it is reached.
import { notFound } from "next/navigation";

export default function MissingPage() {
  notFound();
}
