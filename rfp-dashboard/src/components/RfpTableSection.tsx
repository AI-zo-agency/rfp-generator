import { RfpTable } from "@/components/RfpTable";
import { getRfps } from "@/lib/rfp-service";

export async function RfpTableSection() {
  const allRfps = await getRfps();
  const rfps = allRfps.filter(
    (r) => !["won", "lost", "passed", "submitted"].includes(r.status)
  );

  return <RfpTable rfps={rfps} />;
}
