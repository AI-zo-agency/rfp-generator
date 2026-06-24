export interface JustWinLead {
  externalId: string;
  title: string;
  location: string;
  postedDate: string;
  dueDate: string;
  score: number;
  description: string;
  detailUrl: string;
  tab: "hot" | "warm" | "review";
}
