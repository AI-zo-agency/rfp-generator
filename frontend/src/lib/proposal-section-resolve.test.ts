import { describe, expect, it } from "vitest";
import {
  chatBusyStatusLabel,
  chatLiveWorkSteps,
  messageIsInPlaceBioEdit,
  messageLooksChatQuestion,
  messageLooksOutlineStructure,
  messageLooksStructural,
  messageNeedsCaseStudyClarify,
  messageAsksCaseStudyRelevance,
  messagePointsAtOpenSection,
  pinnedSectionConflictsWithMessage,
  resolveChatTarget,
  resolveSectionFromMention,
  sectionPersonName,
} from "./proposal-section-resolve";
import type { OutlineSection } from "@/types/proposal";

function sec(id: string, title: string, content = "x"): OutlineSection {
  return {
    id,
    title,
    content,
    wordTarget: 500,
    required: true,
    custom: false,
    status: "generated",
    source: "template",
  };
}

describe("chatBusyStatusLabel", () => {
  it("says Answering for questions even when a section is pinned", () => {
    expect(
      chatBusyStatusLabel("what this section about?", "PDF format proposal submission", {
        referenceMode: "section",
        sameSectionPinned: true,
      })
    ).toBe("Answering about PDF format proposal submission…");
  });

  it("still says Improving for explicit improve asks with a pin", () => {
    expect(
      chatBusyStatusLabel("Improve this section for the RFP.", "Who We Are", {
        referenceMode: "section",
        sameSectionPinned: true,
      })
    ).toBe("Improving Who We Are…");
  });

  it("rotates live work steps from the primary status line", () => {
    const steps = chatLiveWorkSteps("Improving Cost Proposal…");
    expect(steps[0]).toBe("Improving Cost Proposal…");
    expect(steps.some((s) => /discrepanc/i.test(s))).toBe(true);
  });

  it("detects question-shaped messages", () => {
    expect(messageLooksChatQuestion("what this section about?")).toBe(true);
    expect(messageLooksChatQuestion("rewrite this to be shorter")).toBe(false);
  });

  it("treats fill this as bound to the open tab", () => {
    expect(messagePointsAtOpenSection("fill this")).toBe(true);
    expect(messagePointsAtOpenSection("fix this table")).toBe(true);
  });
});

describe("resolveSectionFromMention", () => {
  const sections = [
    sec("section-1-who-we-are", "1.1 — Who We Are"),
    sec("section-1-insurance", "1.5 — Insurance Information"),
    sec("section-2-bio-brian", "2.2 — Brian Niles"),
    sec("section-2-bio-rachel", "2.3 — Rachel Rice"),
    sec("section-3-work-oregon", "3.1 — Oregon Employment"),
    sec("section-3-work-umatilla", "3.3 — City of Umatilla Digital Campaign 2006"),
  ];

  it("matches person name even when viewing another section", () => {
    const hit = resolveSectionFromMention(
      sections,
      "Instead of Brian Niles bio add Ron Comer bio",
      "section-1-insurance"
    );
    expect(hit?.id).toBe("section-2-bio-brian");
  });

  it("add another team bio is outline structure (not open-tab rewrite)", () => {
    const result = resolveChatTarget(sections, "add another team bio per RFP", {
      viewingSectionId: "section-1-insurance",
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("outline-structure");
    }
  });
  it("still routes when a case study is named", () => {
    const hit = resolveSectionFromMention(
      sections,
      "rewrite Oregon Employment with more tourism proof",
      "section-1-who-we-are"
    );
    expect(hit?.id).toBe("section-3-work-oregon");
  });

  it("binds open tab by default when user is viewing a section", () => {
    const result = resolveChatTarget(sections, "make this tighter", {
      viewingSectionId: "section-1-insurance",
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-1-insurance");
      expect(result.reason).toBe("viewing-default");
    }
  });

  it("uses open tab only when user says this section", () => {
    const result = resolveChatTarget(sections, "make this section tighter", {
      viewingSectionId: "section-1-insurance",
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-1-insurance");
      expect(result.reason).toBe("viewing-explicit");
    }
  });

  it("resolves section N as manuscript ordinal", () => {
    const long = [
      ...sections,
      sec("rfp-a", "Technical ability"),
      sec("rfp-b", "Past performance"),
      sec("rfp-c", "Cost of base proposal"),
      sec("rfp-d", "Tourism experience"),
      sec("rfp-e", "References"),
      sec("rfp-f", "Agency team"),
      sec("rfp-g", "Geography"),
      sec("rfp-h", "Extra one"),
      sec("rfp-i", "Extra two"),
    ];
    // manuscript order ≈ outline order for these simple stubs
    const result = resolveChatTarget(
      long,
      "can you just replace section 15 with some other new section?",
      { viewingSectionId: "section-1-who-we-are" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("ordinal");
      expect(result.section.id).toBe(long[14]?.id);
    }
  });

  it("parses person name from title", () => {
    expect(sectionPersonName("2.2 — Brian Niles")).toBe("Brian Niles");
  });

  it("detects structural messages", () => {
    expect(
      messageLooksStructural("Instead of Brian Niles bio add Ron Comer")
    ).toBe(true);
  });

  it("bio pin conflict only — not case-study keywords", () => {
    expect(
      pinnedSectionConflictsWithMessage(
        "add another team bio",
        "section-1-who-we-are"
      )
    ).toBe(true);
    expect(
      pinnedSectionConflictsWithMessage(
        "replace existing case studies from KB",
        "section-1-who-we-are"
      )
    ).toBe(false);
  });
});

describe("resolveChatTarget", () => {
  const sections = [
    sec("section-1-who-we-are", "1.1 — Who We Are"),
    sec("section-2-bio-brian", "2.2 — Brian Niles"),
    sec("section-2-bio-rachel", "2.3 — Rachel Rice"),
    sec("section-3-work-oregon", "3.1 — Oregon Employment"),
    sec("section-3-work-san-leandro", "3.2 — Municipality Summ"),
    sec("section-3-work-umatilla", "3.3 — City of Umatilla Digital Campaign 2006"),
  ];

  it("named Past Performance beats pin on another section", () => {
    const withPast = [
      ...sections,
      sec("rfp-sec-2", "Past Performance and References"),
      sec("rfp-sec-3", "Technical Ability"),
    ];
    const pin = withPast.find((s) => s.id === "rfp-sec-3")!;
    const result = resolveChatTarget(
      withPast,
      "Clean the Past Performance and References section so all references are relevant",
      {
        viewingSectionId: "rfp-sec-3",
        pinnedSection: pin,
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-sec-2");
      expect(result.reason).toBe("title");
    }
  });

  it("Improve pin stays on Exhibit when issue list mentions insurance and §18/§26", () => {
    // Reproduce: user pins Exhibit 1 via Improve full section, pastes a defect
    // list that happens to say "professional liability insurance" and compare
    // §18 vs §26 — those incidental words must NOT steal to Insurance / §18.
    const withExhibit = [
      ...sections,
      sec("insurance", "Insurance Certification"),
      sec("sec-18", "18. Evaluation Criteria Narrative"),
      sec("sec-26", "26. Evaluation Criteria Response Form (duplicate)"),
      sec(
        "exhibit-1",
        "Exhibit 1: Evaluation Criteria Response Form"
      ),
    ];
    const pin = withExhibit.find((s) => s.id === "exhibit-1")!;
    const ask = [
      "I.2 Active Client List entirely missing — jumps I.1 → I.3, no client list.",
      "This is the core defect that makes §18 a broken duplicate of §26.",
      'Stray double period — "...professional liability insurance. ."',
      'Dangling empty header — "State registrations." with nothing under it.',
      "Empty table cells in I.4 — 3 of 7 rows have no Cost to CNM / When billed values.",
      "Word count is padded, not complete — repeating I.1/I.3/I.4/I.5 content that already exists in §26.",
      "Fix all of these in this section.",
    ].join("\n");
    const result = resolveChatTarget(withExhibit, ask, {
      viewingSectionId: pin.id,
      pinnedSection: pin,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("exhibit-1");
      expect(result.reason).toBe("pinned");
      expect(result.section.id).not.toBe("insurance");
      expect(result.section.id).not.toBe("sec-18");
    }
  });

  it("Improve pin stays on Technical Proposal when trim ask names §21 Experience", () => {
    // Orland Park: Improve on Technical Proposal + trim instruction that cites
    // §21/§22/Experience as places content already lives — must NOT rewrite Experience.
    const withTech = [
      ...sections,
      sec("tech", "20. Technical Proposal"),
      sec(
        "exp-21",
        "21. Experience — Municipal, Tourism & Economic Development Marketing (Operating History, Relevant Projects & Similar Scope)"
      ),
      sec("qual-22", "22. Qualifications of Assigned Personnel"),
      sec("fee-26", "26. Fee Proposal"),
    ];
    const pin = withTech.find((s) => s.id === "tech")!;
    const ask = [
      "Keep the cross-reference table at the top (RFP Requirement → Addressed In).",
      "Below that, cut the Experience, Operating History, Qualifications table, Approach/Timeline table, and Fee summary — all of that already exists in full in §21/§22/§23/§26.",
      "Before cutting, pull Medford/Rogue X and Santa Clara into §21's case study table;",
      "Letitia Hopper's Qualifications row → fix the blank row in §22.",
    ].join(" ");
    const result = resolveChatTarget(withTech, ask, {
      viewingSectionId: pin.id,
      pinnedSection: pin,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("tech");
      expect(result.reason).toBe("pinned");
      expect(result.section.id).not.toBe("exp-21");
    }
  });

  it("without Improve pin, explicit fix-the-insurance-section still retargets", () => {
    const withInsurance = [
      ...sections,
      sec("insurance", "Insurance Certification"),
      sec("exhibit-1", "Exhibit 1: Evaluation Criteria Response Form"),
    ];
    const result = resolveChatTarget(
      withInsurance,
      "Fix the Insurance Certification section — mark coverages Compliant only from COI",
      {
        viewingSectionId: "exhibit-1",
        pinnedSection: null,
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("insurance");
    }
  });

  it("Improve pin yields only when ask primarily targets another section by name", () => {
    const withInsurance = [
      ...sections,
      sec("insurance", "Insurance Certification"),
      sec("exhibit-1", "Exhibit 1: Evaluation Criteria Response Form"),
    ];
    const pin = withInsurance.find((s) => s.id === "exhibit-1")!;
    const result = resolveChatTarget(
      withInsurance,
      "Fix the Insurance Certification section — mark coverages Compliant only from COI",
      {
        viewingSectionId: pin.id,
        pinnedSection: pin,
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      // Explicit "Fix the Insurance Certification section" is a primary retarget.
      expect(result.section.id).toBe("insurance");
    }
  });

  it("incidental insurance wording without pin does not unique-topic hijack", () => {
    const withInsurance = [
      ...sections,
      sec("insurance", "Insurance Certification"),
      sec("exhibit-1", "Exhibit 1: Evaluation Criteria Response Form"),
    ];
    const result = resolveChatTarget(
      withInsurance,
      "Fix the stray double period after professional liability insurance. .",
      { viewingSectionId: "exhibit-1" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("exhibit-1");
      expect(result.reason).not.toBe("unique-topic");
    }
  });

  it("fuzzy title tokens beat open tab", () => {
    const withPast = [
      ...sections,
      sec("rfp-sec-2", "2 — Past Performance & References"),
      sec("rfp-sec-3", "3 — Technical Ability"),
    ];
    const result = resolveChatTarget(
      withPast,
      "clean past performance references — drop irrelevant ones",
      { viewingSectionId: "rfp-sec-3" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-sec-2");
    }
  });

  it("uses open tab when user says here / in this", () => {
    const result = resolveChatTarget(sections, "here add client voice in this", {
      viewingSectionId: "section-3-work-san-leandro",
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-3-work-san-leandro");
      expect(result.reason).toBe("viewing-explicit");
    }
  });

  it("add client voice for this section is not outline structure", () => {
    expect(
      messageLooksOutlineStructure("here add client voice for this section")
    ).toBe(false);
    const result = resolveChatTarget(
      sections,
      "here add client voice for this section",
      {
        viewingSectionId: "section-3-work-oregon",
        pinnedSection: sections.find((s) => s.id === "section-3-work-oregon"),
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).not.toBe("outline-structure");
      expect(result.section.id).toBe("section-3-work-oregon");
    }
    expect(
      chatBusyStatusLabel("here add client voice for this section", "3.1 — Oregon Employment", {
        referenceMode: "section",
        sameSectionPinned: true,
      })
    ).toBe("Improving 3.1 — Oregon Employment…");
  });

  it("uses explicit pin with high confidence", () => {
    const pin = sections[0];
    const result = resolveChatTarget(sections, "make this tighter", {
      viewingSectionId: "section-3-work-oregon",
      pinnedSection: pin,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-1-who-we-are");
      expect(result.confidence).toBe("high");
      expect(result.reason).toBe("pinned");
    }
  });

  it("resolves named section from query", () => {
    const result = resolveChatTarget(
      sections,
      "rewrite 3.1 — Oregon Employment with more tourism proof",
      { viewingSectionId: "section-1-who-we-are" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-3-work-oregon");
      expect(result.confidence).toBe("high");
    }
  });

  it("asks to confirm when multiple bios match vaguely", () => {
    const result = resolveChatTarget(sections, "improve the Brian bio wait also Rachel", {
      viewingSectionId: "section-1-who-we-are",
    });
    expect(result?.kind).toBe("clarify");
    if (result?.kind === "clarify") {
      expect(result.candidates.length).toBeGreaterThan(1);
      expect(result.question.toLowerCase()).toContain("which");
    }
  });

  it("asks which Our Work piece — does NOT silently use open Who We Are", () => {
    const msg =
      "improve these existing case studies so they suit the rfp requirements";
    expect(messageNeedsCaseStudyClarify(msg)).toBe(true);

    const result = resolveChatTarget(sections, msg, {
      viewingSectionId: "section-1-who-we-are",
    });
    expect(result?.kind).toBe("clarify");
    if (result?.kind === "clarify") {
      expect(result.candidates.some((c) => c.id === "section-3-work-oregon")).toBe(
        true
      );
      expect(result.question.toLowerCase()).toContain("won't guess");
      expect(result.candidates[0].id).not.toBe("section-1-who-we-are");
    }
  });

  it("add new section bypasses open tab and case-study clarify", () => {
    const msg = "add a new section titled Project Staff Planning";
    expect(messageLooksOutlineStructure(msg)).toBe(true);
    expect(messageNeedsCaseStudyClarify(msg)).toBe(false);

    const result = resolveChatTarget(sections, msg, {
      viewingSectionId: "section-1-who-we-are",
      pinnedSection: sections[0],
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("outline-structure");
    }
  });

  it("add one whole new section is outline structure not open-tab bind", () => {
    const msg = "add one whole new section for staff planning";
    expect(messageLooksOutlineStructure(msg)).toBe(true);
    const result = resolveChatTarget(sections, msg, {
      viewingSectionId: "section-3-work-umatilla",
      pinnedSection: sections.find((s) => s.id === "section-3-work-umatilla") ?? null,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("outline-structure");
    }
  });

  it("add new case study is outline structure not clarify", () => {
    const msg = "add a new case study from the knowledge base for tourism";
    expect(messageLooksOutlineStructure(msg)).toBe(true);
    const result = resolveChatTarget(sections, msg, {
      viewingSectionId: "section-1-who-we-are",
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("outline-structure");
    }
  });
  it("pin still allows editing Who We Are about case-study mentions", () => {
    const result = resolveChatTarget(
      sections,
      "weave better case study mentions into this prose",
      {
        viewingSectionId: "section-1-who-we-are",
        pinnedSection: sections[0],
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-1-who-we-are");
      expect(result.reason).toBe("pinned");
    }
  });

  it("proposal-wide review does not bind as open-tab-only", () => {
    const result = resolveChatTarget(
      sections,
      "what's missing from the proposal — trade secrets and terms?",
      { viewingSectionId: "section-1-who-we-are" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("proposal-wide");
    }
  });

  it("cross-section budget contradiction is treated as proposal-wide", () => {
    const result = resolveChatTarget(
      sections,
      "In section 14 pass-through says $325,242.66 but section 18 says $0.00; this contradiction in the budget needs one clean answer.",
      { viewingSectionId: "section-1-who-we-are" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.reason).toBe("proposal-wide");
    }
  });

  it("resolves §21 References by mark number even with a long title", () => {
    const withRefs = [
      ...sections,
      sec("rfp-ref-21", "21. References — Current Clients"),
    ];
    const result = resolveChatTarget(
      withRefs,
      "Fix §21 References only. Do not touch any other section.",
      { viewingSectionId: "section-1-who-we-are" }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-ref-21");
    }
  });

  it("remembers References from prior chat on short follow-up", () => {
    const withRefs = [
      ...sections,
      sec("rfp-ref-21", "21. References — Current Clients"),
    ];
    const result = resolveChatTarget(withRefs, "apply these fixes", {
      viewingSectionId: "section-1-who-we-are",
      conversationHistory: [
        {
          role: "user",
          content:
            "Fix §21 References only. Replace upon request with KB contacts.",
        },
        { role: "assistant", content: "Ready to apply." },
      ],
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-ref-21");
      expect(result.reason).toBe("chat-history");
    }
  });

  it("does not route Umatilla ask to References via incidental mention", () => {
    const withRefs = [
      ...sections,
      sec("rfp-ref-21", "21. References — Current Clients"),
    ];
    const ask =
      "1. Section 11 (Umatilla case study) still misrepresents what the engagement " +
      "actually was. I flagged this before the References fix, and it hasn't been " +
      "addressed. Needs a Rock the Locks rewrite.";
    const result = resolveChatTarget(withRefs, ask, {
      viewingSectionId: "rfp-ref-21",
      pinnedSection: withRefs.find((s) => s.id === "rfp-ref-21") ?? null,
      conversationHistory: [
        {
          role: "user",
          content: "Fix §21 References only. Replace upon request.",
        },
      ],
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-3-work-umatilla");
      expect(["client-name", "title"]).toContain(result.reason);
    }
  });

  it("rfp fit eval on tourism tab stays open — Umatilla name does not hijack", () => {
    const withRefs = [
      ...sections,
      sec(
        "rfp-tourism-sm",
        "Examples of Tourism or Destination Marketing Social Media Accounts Managed"
      ),
    ];
    const umatilla = withRefs.find((s) => s.id === "section-3-work-umatilla")!;
    const tourism = withRefs.find((s) => s.id === "rfp-tourism-sm")!;
    const ask =
      "in this case study is Umatilla best suited for this rfp case?";
    const result = resolveChatTarget(withRefs, ask, {
      viewingSectionId: tourism.id,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-tourism-sm");
      expect(result.reason).toBe("viewing-explicit");
      expect(result.section.id).not.toBe(umatilla.id);
    }
  });

  it("generic question on open tab stays there even when client is mentioned", () => {
    const withRefs = [
      ...sections,
      sec("rfp-tourism-sm", "Tourism Social Media Examples"),
    ];
    const tourism = withRefs.find((s) => s.id === "rfp-tourism-sm")!;
    const result = resolveChatTarget(
      withRefs,
      "fetch KPIs for San Francisco Travel and fill the empty block here",
      { viewingSectionId: tourism.id }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-tourism-sm");
    }
  });

  it("Improve this section pin beats chat-history References", () => {
    const withRefs = [
      ...sections,
      sec("rfp-ref-21", "21. References — Current Clients"),
    ];
    const umatilla = withRefs.find((s) => s.id === "section-3-work-umatilla")!;
    const result = resolveChatTarget(
      withRefs,
      "Improve this section for the RFP.",
      {
        viewingSectionId: umatilla.id,
        pinnedSection: umatilla,
        conversationHistory: [
          {
            role: "user",
            content: "Fix §21 References only. Replace upon request.",
          },
        ],
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("section-3-work-umatilla");
      expect(result.reason).toBe("pinned");
    }
  });

  it("pin beats history for is-this-accurate follow-ups", () => {
    const withRefs = [
      ...sections,
      sec("rfp-ref", "Client References"),
      sec("schedules", "18. Monthly Social Media Posting Schedules"),
    ];
    const schedules = withRefs.find((s) => s.id === "schedules")!;
    const result = resolveChatTarget(
      withRefs,
      "is this all accurate information cross verify from brain!",
      {
        viewingSectionId: schedules.id,
        pinnedSection: schedules,
        conversationHistory: [
          {
            role: "user",
            content: "remove those verify tags in Client references",
          },
          {
            role: "assistant",
            content: "Removed VERIFY tags from Client References.",
          },
        ],
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("schedules");
      expect(result.reason).toBe("pinned");
    }
  });

  it("viewing tab beats history when asking is this accurate without a pin", () => {
    const withRefs = [
      ...sections,
      sec("rfp-ref", "Client References"),
      sec("schedules", "18. Monthly Social Media Posting Schedules"),
    ];
    const schedules = withRefs.find((s) => s.id === "schedules")!;
    const result = resolveChatTarget(
      withRefs,
      "is this all accurate information cross verify from brain!",
      {
        viewingSectionId: schedules.id,
        pinnedSection: null,
        conversationHistory: [
          {
            role: "user",
            content: "remove those verify tags in Client references",
          },
        ],
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("schedules");
      expect(result.reason).toBe("viewing-this");
    }
  });

  it("implement budget table here does not steal to Cost Proposal", () => {
    const withCost = [
      ...sections,
      sec("rfp-cost", "Cost of Base Proposal / Fee Schedule"),
      sec(
        "compliance",
        "General Requirements Compliance Statement — SOW, Timelines, Budgets"
      ),
    ];
    const compliance = withCost.find((s) => s.id === "compliance")!;
    const result = resolveChatTarget(withCost, "implement budget table here", {
      viewingSectionId: compliance.id,
      pinnedSection: compliance,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("compliance");
      expect(result.reason).not.toBe("unique-topic");
    }
  });

  it("clarify reply does not follow stale Cover Letter Improve pin", () => {
    const withCompliance = [
      sec("cover", "Cover Letter & Executive Summary"),
      ...sections,
      sec(
        "compliance",
        "General Requirements Compliance Statement — SOW, Timelines, Budgets, Reporting, Records Retention (Section II)"
      ),
    ];
    const cover = withCompliance.find((s) => s.id === "cover")!;
    const compliance = withCompliance.find((s) => s.id === "compliance")!;
    const result = resolveChatTarget(
      withCompliance,
      "Section 13 General Requirements Compliance Statement (currently open in the UI)",
      {
        viewingSectionId: compliance.id,
        pinnedSection: cover,
        conversationHistory: [
          {
            role: "user",
            content: "Improve this section for the RFP. Its not correct budget",
          },
          {
            role: "assistant",
            content:
              "Which section should I improve?\n\n" +
              "1. **Section 1.3 Business Information**\n" +
              "2. **Section 8 Proposal Pricing — Hourly Rates**\n" +
              "3. **Section 13 General Requirements Compliance Statement (currently open in the UI)**\n" +
              "4. **Case study budget table**\n",
          },
        ],
      }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("compliance");
      expect(result.reason).not.toBe("pinned");
    }
  });

  it("matches long compliance title from clarify head phrase", () => {
    const withCompliance = [
      sec("cover", "Cover Letter & Executive Summary"),
      sec(
        "compliance",
        "General Requirements Compliance Statement — SOW, Timelines, Budgets, Reporting, Records Retention (Section II)"
      ),
    ];
    const result = resolveChatTarget(
      withCompliance,
      "General Requirements Compliance Statement",
      { viewingSectionId: "cover", pinnedSection: withCompliance[0] }
    );
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("compliance");
    }
  });

  it("experience of personnel resume fetch stays on open tab — no Section 2 bio picker", () => {
    const personnel = sec(
      "rfp-sec-23",
      "23 Experience of Personnel",
      "### Shawn DiCriscio\n**Role on this engagement:** Lead WordPress developer.\n"
    );
    const withPersonnel = [
      ...sections,
      personnel,
      sec("section-2-bio-ella", "2.1 — Ella Lindau"),
      sec("section-2-bio-curt", "2.2 — Curt Schultz"),
    ];
    const msg =
      "here in shawn DiCriscio make sure you fetch correct info from its resume and update it";
    expect(messageIsInPlaceBioEdit(msg)).toBe(true);
    const result = resolveChatTarget(withPersonnel, msg, {
      viewingSectionId: personnel.id,
    });
    expect(result?.kind).toBe("resolved");
    if (result?.kind === "resolved") {
      expect(result.section.id).toBe("rfp-sec-23");
      expect(result.reason).toBe("in-place-bio");
    }
  });
});

describe("messageAsksCaseStudyRelevance", () => {
  it("detects relevance intent", () => {
    expect(messageAsksCaseStudyRelevance("is this case study relevant?")).toBe(true);
    expect(messageAsksCaseStudyRelevance("strengthen relevance to the rfp")).toBe(true);
    expect(messageAsksCaseStudyRelevance("suggest a better case study")).toBe(true);
    expect(messageAsksCaseStudyRelevance("swap this case study")).toBe(true);
    expect(messageAsksCaseStudyRelevance("add the best-matching case studies from the knowledge base")).toBe(true);
    expect(messageAsksCaseStudyRelevance("tie outcomes to requirements")).toBe(true);
  });

  it("ignores non-relevance case study mentions", () => {
    expect(messageAsksCaseStudyRelevance("rewrite this case study")).toBe(false);
    expect(messageAsksCaseStudyRelevance("fix typos in the case study")).toBe(false);
  });
});
