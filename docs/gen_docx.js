const fs = require("fs");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
        ShadingType, PageNumber, PageBreak, LevelFormat } = require("docx");

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellM = { top: 80, bottom: 80, left: 120, right: 120 };

function hdrCell(text, width) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: { fill: "1B3A5C", type: ShadingType.CLEAR },
    margins: cellM,
    children: [new Paragraph({ children: [new TextRun({ text, bold: true, color: "FFFFFF", font: "Arial", size: 20 })] })]
  });
}
function cell(text, width, opts = {}) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: opts.shade ? { fill: opts.shade, type: ShadingType.CLEAR } : undefined,
    margins: cellM,
    children: [new Paragraph({ children: [new TextRun({ text, font: "Arial", size: 20, bold: opts.bold || false, color: opts.color })] })]
  });
}
function r(cells) { return new TableRow({ children: cells }); }

function bullet(text) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: [new TextRun(text)] });
}
function numbered(text) {
  return new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 100 }, children: [new TextRun(text)] });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: "1B3A5C" },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: "2E75B6" },
        paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "404040" },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
    ]
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        children: [new TextRun({ text: "ContextPulse Market Research & Feature Roadmap", font: "Arial", size: 16, color: "999999", italics: true })],
        alignment: AlignmentType.RIGHT
      })] })
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        children: [
          new TextRun({ text: "Jerard Ventures LLC | Confidential | Page ", font: "Arial", size: 16, color: "999999" }),
          new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: "999999" })
        ],
        alignment: AlignmentType.CENTER
      })] })
    },
    children: [
      // TITLE PAGE
      new Paragraph({ spacing: { before: 3000 }, children: [] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [
        new TextRun({ text: "ContextPulse", font: "Arial", size: 56, bold: true, color: "1B3A5C" })
      ] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 }, children: [
        new TextRun({ text: "Market Research & Feature Roadmap", font: "Arial", size: 32, color: "2E75B6" })
      ] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 400 }, children: [
        new TextRun({ text: "Always-on context for AI agents", font: "Arial", size: 24, italics: true, color: "666666" })
      ] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 }, children: [
        new TextRun({ text: "March 24, 2026", font: "Arial", size: 22, color: "666666" })
      ] }),
      new Paragraph({ alignment: AlignmentType.CENTER, children: [
        new TextRun({ text: "Jerard Ventures LLC | Confidential", font: "Arial", size: 20, color: "999999" })
      ] }),

      new Paragraph({ children: [new PageBreak()] }),

      // EXECUTIVE SUMMARY
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Executive Summary")] }),
      new Paragraph({ spacing: { after: 200 }, children: [
        new TextRun("ContextPulse is the only product combining screen capture, voice dictation, and keyboard/mouse input capture in a single, unified process. With 23 MCP tools, sub-1% CPU overhead, and complete local privacy, it fills a market gap left by Rewind/Limitless (acquired by Meta, Dec 2025) and underserved by Screenpipe ($400, 5-15% CPU), Wispr Flow (cloud-only, $15/mo), and Pieces for Developers (no screen/voice capture).")
      ] }),
      new Paragraph({ spacing: { after: 200 }, children: [
        new TextRun("The product is production-testable today: unified daemon, Windows installer (110MB), 726 tests passing, three modality modules running simultaneously with a shared EventBus spine.")
      ] }),

      new Paragraph({ children: [new PageBreak()] }),

      // COMPETITIVE LANDSCAPE
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Competitive Landscape")] }),
      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [1800, 1500, 1200, 1200, 1560, 2100],
        rows: [
          r([hdrCell("Competitor",1800), hdrCell("Category",1500), hdrCell("Platform",1200), hdrCell("Pricing",1200), hdrCell("Status",1560), hdrCell("Key Threat",2100)]),
          r([cell("Limitless (Rewind)",1800,{bold:true}), cell("Screen + AI",1500), cell("Mac (was)",1200), cell("$20/mo+$99",1200), cell("DEAD",1560,{color:"CC0000",bold:true}), cell("Acquired by Meta Dec 2025",2100)]),
          r([cell("Screenpipe",1800,{bold:true}), cell("Open-source screen+audio",1500), cell("Win/Mac/Linux",1200), cell("$400 lifetime",1200), cell("Pivoting",1560,{color:"CC8800"}), cell("5-15% CPU, pivoting to automation",2100)]),
          r([cell("Microsoft Recall",1800,{bold:true}), cell("OS-level screen",1500), cell("Copilot+ PCs",1200), cell("Free (HW gated)",1200), cell("Gated",1560,{color:"CC8800"}), cell("Requires 40+ TOPS NPU",2100)]),
          r([cell("Pieces for Devs",1800,{bold:true}), cell("Context copilot",1500), cell("Win/Mac/Linux",1200), cell("Free/$19/mo",1200), cell("Active",1560,{color:"008800",bold:true}), cell("LTM-2 (9-month memory) + MCP",2100)]),
          r([cell("Wispr Flow",1800,{bold:true}), cell("Voice dictation",1500), cell("Mac/Win/iOS",1200), cell("$12-15/mo",1200), cell("Active",1560,{color:"008800",bold:true}), cell("Command Mode, 97.2% accuracy",2100)]),
          r([cell("Granola",1800,{bold:true}), cell("Meeting notes",1500), cell("Mac/Win",1200), cell("Free/$18/mo",1200), cell("Active",1560,{color:"008800",bold:true}), cell("$250M valuation validates category",2100)]),
        ]
      }),

      // WHERE WE WIN
      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("Where ContextPulse Wins")] }),
      bullet("Unified modalities \u2014 the only product combining screen + voice + touch in one process"),
      bullet("MCP-native \u2014 23 tools designed for AI agents from day one"),
      bullet("Cross-modal search \u2014 search across what you saw, said, AND typed simultaneously"),
      bullet("Self-improving voice \u2014 Touch detects dictation corrections and auto-learns vocabulary"),
      bullet("Privacy-first \u2014 100% local, blocklist, auto-pause on lock, OCR redaction"),
      bullet("Sub-1% CPU \u2014 no competitor comes close; architectural advantage"),

      // WHERE THEY WIN
      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("Where Competitors Win")] }),
      bullet("Screenpipe: Open source, REST API, Linux support, audio capture"),
      bullet("Wispr Flow: Command Mode, 100+ languages, quiet Whisper Mode, polished UX"),
      bullet("Pieces: 9-month rolling memory (LTM-2), snippet management, IDE plugins"),
      bullet("Granola: $250M valuation proves ambient AI context has premium multiples"),

      new Paragraph({ children: [new PageBreak()] }),

      // FEATURE ROADMAP
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Feature Roadmap (Ranked by Value)")] }),

      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("Tier 1: Ship Next (Score 8-10)")] }),
      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [500, 1800, 3760, 600, 700, 1000, 1000],
        rows: [
          r([hdrCell("#",500), hdrCell("Feature",1800), hdrCell("Description",3760), hdrCell("Value",600), hdrCell("Effort",700), hdrCell("From",1000), hdrCell("Tier",1000)]),
          r([cell("1",500), cell("Voice Command Mode",1800,{bold:true}), cell("'Delete last sentence', 'make this a list', 'translate to Spanish'",3760), cell("10",600,{bold:true,color:"008800"}), cell("M",700), cell("Wispr",1000), cell("Pro",1000)]),
          r([cell("2",500), cell("Cross-Session Memory",1800,{bold:true}), cell("Persistent context surviving restarts",3760), cell("10",600,{bold:true,color:"008800"}), cell("L",700), cell("Pieces",1000), cell("Pro",1000)]),
          r([cell("3",500), cell("System Audio",1800,{bold:true}), cell("Record audio + mic for meeting transcription",3760), cell("9",600,{bold:true,color:"008800"}), cell("M",700), cell("Screenpipe",1000), cell("Pro",1000)]),
          r([cell("4",500), cell("Onboarding",1800,{bold:true}), cell("First-run wizard: test mic, hotkeys, privacy",3760), cell("9",600,{bold:true,color:"008800"}), cell("S",700), cell("Wispr",1000), cell("Free",1000)]),
          r([cell("5",500), cell("Activity Timeline",1800,{bold:true}), cell("Visual timeline of apps, dictations, screenshots",3760), cell("9",600,{bold:true,color:"008800"}), cell("M",700), cell("Limitless",1000), cell("Free",1000)]),
        ]
      }),

      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("Tier 2: Build Soon (Score 6-8)")] }),
      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [500, 1800, 3760, 600, 700, 1000, 1000],
        rows: [
          r([hdrCell("#",500), hdrCell("Feature",1800), hdrCell("Description",3760), hdrCell("Value",600), hdrCell("Effort",700), hdrCell("From",1000), hdrCell("Tier",1000)]),
          r([cell("6",500), cell("Multi-Language",1800,{bold:true}), cell("Auto-detect 100+ languages (Whisper supports it)",3760), cell("8",600), cell("S",700), cell("Wispr",1000), cell("Free",1000)]),
          r([cell("7",500), cell("REST API",1800,{bold:true}), cell("localhost HTTP API for non-MCP tools",3760), cell("8",600), cell("M",700), cell("Screenpipe",1000), cell("Pro",1000)]),
          r([cell("8",500), cell("IDE Plugins",1800,{bold:true}), cell("VS Code / JetBrains context sidebar",3760), cell("8",600), cell("L",700), cell("Pieces",1000), cell("Pro",1000)]),
          r([cell("9",500), cell("Smart Snippets",1800,{bold:true}), cell("Auto-save code from OCR with language tags",3760), cell("7",600), cell("M",700), cell("Pieces",1000), cell("Pro",1000)]),
          r([cell("10",500), cell("Whisper Mode",1800,{bold:true}), cell("Low-volume dictation for open offices",3760), cell("7",600), cell("S",700), cell("Wispr",1000), cell("Free",1000)]),
          r([cell("11",500), cell("macOS Port",1800,{bold:true}), cell("Unlocks 30-40% of developer market",3760), cell("7",600), cell("L",700), cell("Screenpipe",1000), cell("Free",1000)]),
        ]
      }),

      new Paragraph({ children: [new PageBreak()] }),

      // REVENUE
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Revenue Strategy")] }),
      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [1500, 1200, 6660],
        rows: [
          r([hdrCell("Tier",1500), hdrCell("Price",1200), hdrCell("Includes",6660)]),
          r([cell("Free",1500,{bold:true}), cell("$0",1200), cell("Sight (screen, OCR, clipboard), Voice (basic dictation), Touch (typing/mouse), 10 MCP tools",6660)]),
          r([cell("Starter",1500,{bold:true}), cell("$8/mo",1200), cell("Cross-modal search, REST API, multi-language voice, 7-day memory",6660)]),
          r([cell("Pro",1500,{bold:true}), cell("$19/mo",1200), cell("Unlimited memory, Command Mode, audio capture, IDE plugins, attention scoring",6660)]),
        ]
      }),
      new Paragraph({ spacing: { before: 200 }, children: [
        new TextRun({ text: "Comparable pricing: ", bold: true }),
        new TextRun("Wispr Flow $12-15/mo, Pieces Pro $19/mo, Limitless was $20/mo + $99 hardware")
      ] }),

      // BUILD ORDER
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Recommended Build Order (Q2 2026)")] }),
      numbered("Onboarding Polish (S) \u2014 first impressions matter, needed before any launch"),
      numbered("Voice Command Mode (M) \u2014 #1 feature to make users switch from Wispr Flow"),
      numbered("Activity Timeline UI (M) \u2014 makes the product tangible and shareable"),
      numbered("Multi-Language Voice (S) \u2014 quick win, Whisper already supports 100+ languages"),
      numbered("Cross-Session Memory (L) \u2014 killer feature that justifies Pro at $19/mo"),
      numbered("System Audio Capture (M) \u2014 unlocks meeting transcription use case"),
      numbered("REST API (M) \u2014 opens integrations beyond MCP"),

      // DEVELOPER PSYCHOLOGY
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Developer Psychology")] }),
      new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("What Makes Developers LOVE It")] }),
      bullet("Screen narration \u2014 agents understand the screen without image tokens"),
      bullet("Voice dictation \u2014 4x faster than typing for thoughts and annotations"),
      bullet("Contextual annotations \u2014 bookmark a screen moment with one hotkey"),
      bullet("Session summaries \u2014 'what did I work on today?' answered automatically"),

      new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("What Makes Developers PAY")] }),
      bullet("Memory module \u2014 eliminates repetitive context-setting, saves 15-30 min/day"),
      bullet("Screen-aware vocabulary \u2014 dramatically improves dictation accuracy for code"),
      bullet("Meeting transcription \u2014 replaces $8-20/mo Otter/Granola with local, private alternative"),

      new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("What Makes Developers TELL OTHERS")] }),
      bullet("Sub-1% CPU \u2014 'it runs and I forget it's there'"),
      bullet("Pre-storage redaction \u2014 'it redacts API keys before they hit disk'"),
      bullet("Cross-modal learning \u2014 'my voice got better because it reads my screen'"),
      bullet("$19/mo replaces $15/mo Wispr + $18/mo Granola \u2014 compelling value story"),
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = "C:\\Users\\david\\Projects\\ContextPulse\\docs\\ContextPulse-Market-Research.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("Created: " + outPath);
});
