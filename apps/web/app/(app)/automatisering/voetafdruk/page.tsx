// Automatisering → Voetafdruk (1.6.0, C35): what MSO changed in your systems right now, origineel → MSO → nu, your own
// choices that MSO respects, and "Alles terugzetten". Data: GET /api/v1/automation/footprint (+ …/refresh, …/restore).
"use client";

import { FootprintPage } from "@/components/mso/automation/footprint";

export default function VoetafdrukPage() {
  return <FootprintPage />;
}
