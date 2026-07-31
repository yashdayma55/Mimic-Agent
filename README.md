# MimicAgent

MimicAgent is a desktop agent that learns a computer workflow by watching you do it once, and then does it for you.

There is no scripting and no prompting. You press a Learn button, you go through your task the way you normally would, and the agent watches quietly in the background. It notices where you click, what you type, and which actual interface element you touched at each step. When you are done it turns that recording into a plan written in plain language that you can read and edit. Later you press Play, feed it a new input, and it walks through the same workflow, stopping at every step to show you what it is about to do and waiting for your go-ahead. If it gets a step wrong you stop it, tell it what went wrong in normal words, and it fixes that step and remembers the fix so it does not make the same mistake again.

The whole thing runs on your own machine. No cloud, no API keys, no per-token cost. It is built to stay light enough to run on an ordinary 16GB laptop without turning the fans on.

The example I built it around is my own job hunt. The workflow is: find a recruiter who is hiring on LinkedIn, pull their email through a browser extension, draft an outreach message and a resume tailored to the role, and save everything into a folder named after the company. That is a tedious loop I run over and over, which makes it the perfect thing to hand to an agent. Nothing about the design is specific to job applications though. Any repetitive multi step desktop task fits the same mold.

## Why I built it the way I did

Most tools that automate a screen do it by remembering pixel positions. Click at x=340, y=220. That breaks the instant a window moves or a page reflows, which on a real website is constantly. So MimicAgent does not lead with pixels. It leads with the accessibility tree, which is the structured description of the interface that the operating system already keeps for screen readers. When you click a button, Windows already knows it is a button named "Connect", and I can read that in a couple of milliseconds without looking at a single pixel. That semantic information is what makes a recording survive into tomorrow, when the layout has shifted slightly but the button named "Connect" is still called "Connect".

Vision only comes in as a backup. Some apps draw their own interface onto a blank canvas and expose nothing to the accessibility tree. For those cases, and only those, a local vision model looks at a screenshot and finds the element. Keeping the slow, heavy vision step off the main path is the entire reason this can run offline on a normal laptop instead of needing a datacenter GPU.

## The big picture

Here is the whole system across all four stages. Follow the thick arrows for the main path, and the dotted arrows for the moments it leans on the local vision model.

```mermaid
flowchart TB
    subgraph LEARN["LEARN - you demonstrate once"]
        direction LR
        L1["pynput<br/>mouse + keys"]:::rec
        L2["mss<br/>screenshots"]:::rec
        L3["pywinauto<br/>Windows UI tree"]:::rec
        L4["Playwright<br/>browser UI tree"]:::rec
    end

    DB[("SQLite - WAL mode<br/>events - screenshots - plans - corrections<br/>the single source of truth")]:::db

    subgraph BRAIN["Local Vision Model - Ollama + Qwen3-VL - fully offline"]
        direction LR
        B1["summarize<br/>the recording"]:::ai
        B2["find element<br/>by sight (fallback)"]:::ai
        B3["understand<br/>your corrections"]:::ai
    end

    subgraph DISTILL["DISTILL - turn recording into a plan"]
        direction TB
        D1["group raw clicks<br/>into real steps"]:::dist
        D2["write readable notes<br/>+ an execution graph"]:::dist
        D1 --> D2
    end

    EDIT{{"You review and edit<br/>the plan like a document"}}:::human

    subgraph REPLAY["REPLAY - it does the task, you approve each step"]
        direction TB
        R1["find the target<br/>5-tier self-healing locator"]:::rep
        R2["highlight it and<br/>wait for your OK"]:::rep
        R3["act, then verify<br/>the screen changed"]:::rep
        R1 --> R2 --> R3
    end

    subgraph CORRECT["CORRECT - it learns from your feedback"]
        direction TB
        C1["you say what<br/>went wrong"]:::cor
        C2["it patches the step<br/>and remembers it"]:::cor
        C1 --> C2
    end

    LEARN ==> DB
    DB ==> DISTILL
    DISTILL -.uses.-> B1
    DISTILL ==> EDIT
    EDIT ==> REPLAY
    REPLAY -.fallback.-> B2
    REPLAY ==> CORRECT
    CORRECT -.uses.-> B3
    CORRECT ==>|"resume from the fixed step"| REPLAY
    CORRECT -.saves.-> DB

    classDef rec fill:#e8f5e9,color:#1b5e20,stroke:#66bb6a,stroke-width:1.5px
    classDef dist fill:#fff8e1,color:#795548,stroke:#ffca28,stroke-width:1.5px
    classDef rep fill:#e3f2fd,color:#0d47a1,stroke:#42a5f5,stroke-width:1.5px
    classDef cor fill:#fce4ec,color:#880e4f,stroke:#ec407a,stroke-width:1.5px
    classDef ai fill:#ede7f6,color:#311b92,stroke:#7e57c2,stroke-width:1.5px
    classDef db fill:#263238,color:#fff,stroke:#000,stroke-width:2px
    classDef human fill:#fff3e0,color:#e65100,stroke:#ff9800,stroke-width:2px

    style LEARN fill:#f1f8f2,stroke:#66bb6a,stroke-width:2px
    style DISTILL fill:#fffdf5,stroke:#ffca28,stroke-width:2px
    style REPLAY fill:#f3f9ff,stroke:#42a5f5,stroke-width:2px
    style CORRECT fill:#fef4f7,stroke:#ec407a,stroke-width:2px
    style BRAIN fill:#f6f3fc,stroke:#7e57c2,stroke-width:2px
```

The green Learn block is four listeners running at once while you work. Everything they see flows into the SQLite database in the middle, which is the single place the whole system reads from and writes to. Once you stop recording, the yellow Distill block reads the raw events back out, groups clusters of clicks into clean readable steps, and produces two forms of the same workflow: notes you can edit, and a structured plan the executor can run. You get to review and edit those notes before anything runs. The blue Replay block then walks the plan one step at a time, finding each target, highlighting it, waiting for your approval, acting, and checking the screen actually changed before moving on. The red Correct block is where your feedback rewrites a step and gets saved into memory, so the resume picks up from the fixed step and the lesson is remembered next time. The purple block is the local vision model, and the dotted lines show it is only pulled in for three specific jobs rather than running constantly.

## Where the project is right now

Three phases are built and working, in order.

- **Phase 1 - Recorder** (done): captures any workflow to a local database.
- **Phase 2 - Local vision** (done): understands screenshots offline and identifies UI elements as structured data.
- **Phase 3 - Distillation** (done): turns a raw recording into a readable, editable, security-conscious plan.

So today MimicAgent can already **watch** a task, **understand** what is on screen, and **plan** the workflow as clean steps. What remains is the replay engine that executes the plan, the correction loop that learns from feedback, and packaging. Those are described in the roadmap at the end.

Each finished phase is walked through in detail below.

---

## Phase 1 in detail: the recorder

The recorder has one job: watch you work and write down everything that matters, without ever slowing your machine down. That last part sounds easy and is actually the whole challenge, so the design is built around it.

Here is exactly what happens, start to finish, every time you click something.

```mermaid
flowchart TB
    START(["You press Learn<br/>and start working"]):::start

    subgraph CATCH["1 - CATCH THE ACTION (instant, never lags your mouse)"]
        direction TB
        CLICK["You click or type<br/>somewhere on screen"]:::action
        HOOK["pynput global hook<br/>sees it system-wide,<br/>even in other apps"]:::tool
        DROP["Drop a tiny note on the queue<br/>and return immediately"]:::tool
        CLICK --> HOOK --> DROP
    end

    QUEUE{{"QUEUE<br/>events wait here in line<br/>so nothing is lost"}}:::queue

    subgraph WORK["2 - DO THE SLOW WORK (on a separate thread)"]
        direction TB
        PULL["Writer thread pulls<br/>the next event"]:::tool
        LOOK["pywinauto asks Windows:<br/>what element is here?<br/>e.g. 'Connect' Button"]:::tool
        SHOT["mss grabs a screenshot<br/>of the screen right now"]:::tool
        PULL --> LOOK --> SHOT
    end

    subgraph STORE["3 - SAVE IT FOREVER"]
        direction TB
        DB[("SQLite database<br/>WAL mode:<br/>time, element, coords,<br/>key, screenshot path")]:::db
    end

    RESULT(["recording.db holds your<br/>whole workflow as data"]):::start

    START --> CLICK
    DROP --> QUEUE
    QUEUE --> PULL
    SHOT --> DB
    DB --> RESULT

    classDef start fill:#2d3561,color:#fff,stroke:#1a1f3a,stroke-width:2px
    classDef action fill:#ffe0b2,color:#5d4037,stroke:#e8a04a,stroke-width:2px
    classDef tool fill:#e3f2fd,color:#1a3a5c,stroke:#5b9bd5,stroke-width:1.5px
    classDef queue fill:#fff3cd,color:#7a5c00,stroke:#e0a800,stroke-width:2px
    classDef db fill:#d5f0e0,color:#1b5e3f,stroke:#48a877,stroke-width:2px
```

The diagram splits into three stages, and the split is the important idea.

Stage one catches the action. When you click or type anywhere, a pynput global hook picks it up. A global hook means it sees your input no matter which app is focused, because it taps into the input at the operating system level rather than waiting for its own window to be active. This is what lets it watch you work in Chrome and Gmail and File Explorer even though the recorder itself is just running in the background. The one firm rule here is that this step has to be nearly instant. The hook callback runs inside the same pipeline the OS uses to deliver your click to the app you clicked on, so if the callback does anything slow, your actual mouse freezes for the whole system. So all it does is jot down a tiny note about the event and drop it on a queue, then get out of the way immediately.

The queue in the middle is the trick that makes the whole thing smooth. It is just a line that events wait in. The fast side drops events onto it and never waits. The slow side picks them up whenever it is ready. If you click five times quickly, all five land in the queue instantly and get processed one by one afterward, so nothing is ever lost and nothing ever lags.

Stage two does the slow work, and it runs on a completely separate thread so none of it can touch your mouse. A writer thread pulls the next event off the queue and does two genuinely slow things. First it asks Windows, through pywinauto and the UI Automation system, what interface element sits at those coordinates, and gets back something meaningful like the button named "Connect". This is the step that turns a meaningless coordinate into something the agent can actually understand and find again later. Second it takes a screenshot of the screen at that exact moment with mss, so there is a visual record of the situation, which becomes the backup for any element the accessibility tree could not name.

Stage three saves everything into a SQLite database running in write ahead logging mode. Write ahead logging lets the writer keep saving while other parts of the program read at the same time, and it keeps everything already written safe even if the program crashes in the middle of a recording. Each row holds the timestamp, the kind of event, the coordinates, the key if it was a keystroke, the name and type of the element, and the path to the screenshot.

When you press Escape to stop, the result is recording.db: your entire workflow captured as clean, queryable data, with every action tied to the element it touched and a picture of the screen at the time.

The one sentence summary worth remembering: listeners catch events, a queue passes them safely to a writer thread, and the writer thread saves the events, screenshots, and interface information into SQLite.

**What a real capture looks like.** In one test run the recorder watched me open a recruiter's LinkedIn profile, click the Apollo extension to find their email, switch over to Gmail, hit Compose, and paste into the recipient field. Every one of those actions came back out of the database afterward with a timestamp, the name of the element I touched, and a screenshot, and my mouse never lagged once through the whole thing. That is the moment the project stopped being an idea and started being real.

---

## Phase 2 in detail: local vision

Phase 1 gave the agent a memory of what happened, but a screenshot is just pixels. Phase 2 gives the agent eyes: a vision model that runs entirely on your own machine and can look at a screenshot and say what a UI element is, returning the answer as structured data the rest of the agent can act on. Everything here is local, so no screen data ever leaves the machine.

```mermaid
flowchart TB
    IN(["A screenshot from the recording<br/>(just pixels, no meaning yet)"]):::start

    subgraph PREP["1 - PREPARE THE IMAGE (make it cheap to process)"]
        direction TB
        CROP["Crop to the region<br/>around the click point<br/><i>far fewer pixels = far faster</i>"]:::tool
        RESIZE["Downscale if still large<br/><i>fewer visual tokens</i>"]:::tool
        CROP --> RESIZE
    end

    subgraph MODEL["2 - LOCAL VISION MODEL (Ollama, fully offline)"]
        direction TB
        WARM["Keep model warm in RAM<br/><i>cold-load is the real cost,<br/>not the image</i>"]:::ai
        ASK["Ask qwen3-vl:2b:<br/>what UI element is this?<br/><i>think=False for speed</i>"]:::ai
        WARM --> ASK
    end

    subgraph OUT["3 - STRUCTURED ANSWER (data, not prose)"]
        direction TB
        JSON["Model returns JSON in the text<br/>element_type / label / confidence"]:::good
        PARSE["Parse from first { to last }<br/><i>tolerant of stray text</i>"]:::good
        JSON --> PARSE
    end

    RESULT(["Usable data:<br/>{element_type: textbox,<br/>label: Write a message,<br/>confidence: high}"]):::start

    IN --> CROP
    RESIZE --> WARM
    ASK --> JSON
    PARSE --> RESULT

    classDef start fill:#311b92,color:#fff,stroke:#1a0f5c,stroke-width:2px
    classDef tool fill:#e3f2fd,color:#1a3a5c,stroke:#5b9bd5,stroke-width:1.5px
    classDef ai fill:#ede7f6,color:#311b92,stroke:#7e57c2,stroke-width:1.5px
    classDef good fill:#e8f5e9,color:#1b5e20,stroke:#66bb6a,stroke-width:1.5px

    style PREP fill:#f3f9ff,stroke:#5b9bd5,stroke-width:2px
    style MODEL fill:#f6f3fc,stroke:#7e57c2,stroke-width:2px
    style OUT fill:#f1f8f2,stroke:#66bb6a,stroke-width:2px
```

The model I settled on is Qwen3-VL at the 2B size, served locally by Ollama. On a 16GB laptop with no GPU, the larger 8B model took around half an hour for a single screenshot, which is unusable, while the 2B model handles the same work in seconds once it is warm. That trade, a smaller model that fits the hardware, is the whole reason the offline promise holds.

The biggest lesson of this phase was where the time actually goes. I assumed the image size or the inference itself was the cost, but it turned out the dominant cost was cold-loading the model from disk into memory. Once the model is warm in RAM, a cropped screenshot comes back in single-digit to low-double-digit seconds; cold, it takes minutes. So the first stage of the diagram, preparing the image by cropping to the region around the click, matters less for raw speed than keeping the model warm, though cropping still helps by cutting the number of visual tokens the model has to chew through. On a machine this tight, warmth cannot be perfectly guaranteed because the operating system reclaims memory for other things, which causes occasional slow spikes, but that is acceptable because vision never runs where a user is waiting.

The third stage is the one that makes vision genuinely useful. The agent cannot act on a paragraph of description; it needs data. So instead of asking the model to describe the screen, I ask it to identify the element and return a small JSON object with an element type, a label, and a confidence. A subtle but important finding here: Ollama has a forced-JSON mode, but with this small vision model it returned empty output, so the reliable approach is to describe the exact JSON shape in the prompt, let the model produce it as normal text, and then parse it by taking everything from the first brace to the last. That tolerates any stray text the model adds around the JSON.

The reason all of this is designed as an off-to-the-side, occasional step is the core architectural bet of the whole project: the accessibility tree already names most elements for free and instantly, so vision is reserved for the small fraction of cases where the tree came back empty. Phase 3 is where that split shows up as a real number.

---

## Phase 3 in detail: distillation

Distillation is the step that turns the raw recording into a plan a human can read and edit. The recorder is faithful but noisy: typing one sentence is dozens of separate keystroke events, holding a modifier key fires it dozens of times through auto-repeat, and there are stray clicks and corrections everywhere. A useful plan should not have hundreds of raw events; it should have the handful of meaningful steps you actually intended. Distillation boils the one down to the other.

```mermaid
flowchart TB
    IN[("recording.db<br/>640 raw events<br/>every click and keystroke")]:::start

    subgraph GROUP["1 - GROUP THE NOISE INTO STEPS (cheap rules, no model)"]
        direction TB
        G1["Rebuild typed text<br/>from raw keystrokes<br/><i>applies backspaces</i>"]:::rule
        G2["Collapse repeated clicks<br/>on the same element into one"]:::rule
        G3["Strip key-spam<br/><i>80 Ctrl repeats become nothing</i>"]:::rule
        G1 --> G2 --> G3
    end

    subgraph SAFE["2 - MASK SECRETS"]
        direction TB
        S1["Detect password fields by name"]:::secret
        S2["Store [SECRET: field] reference,<br/>never the real password"]:::secret
        S1 --> S2
    end

    subgraph LABEL["3 - LABEL EACH STEP (accessibility-first)"]
        direction TB
        L1["Element already named?<br/>Label it instantly, free<br/><i>~86% of steps</i>"]:::free
        L2["Name empty?<br/>Fall back to vision model<br/><i>~14% of steps</i>"]:::fallback
    end

    subgraph WRITE["4 - WRITE THE PLAN (two forms)"]
        direction TB
        W1["plan.txt<br/>readable, editable by you"]:::out
        W2["plan.json<br/>structured, for the replay engine"]:::out
    end

    IN ==> GROUP
    GROUP ==> SAFE
    SAFE ==> LABEL
    LABEL ==> WRITE

    RESULT(["A clean, editable plan:<br/>174 readable steps<br/>from 640 raw events"]):::start
    WRITE ==> RESULT

    classDef start fill:#795548,color:#fff,stroke:#4e342e,stroke-width:2px
    classDef rule fill:#fff8e1,color:#795548,stroke:#ffca28,stroke-width:1.5px
    classDef secret fill:#fce4ec,color:#880e4f,stroke:#ec407a,stroke-width:1.5px
    classDef free fill:#e8f5e9,color:#1b5e20,stroke:#66bb6a,stroke-width:1.5px
    classDef fallback fill:#ede7f6,color:#311b92,stroke:#7e57c2,stroke-width:1.5px
    classDef out fill:#e3f2fd,color:#0d47a1,stroke:#42a5f5,stroke-width:1.5px

    style GROUP fill:#fffdf5,stroke:#ffca28,stroke-width:2px
    style SAFE fill:#fef4f7,stroke:#ec407a,stroke-width:2px
    style LABEL fill:#f6f8f6,stroke:#9e9e9e,stroke-width:2px
    style WRITE fill:#f3f9ff,stroke:#42a5f5,stroke-width:2px
```

The first stage is pure pattern rules, no model needed, which is deliberate because cheap and deterministic logic should do the bulk of the cleanup. Consecutive keystrokes are gathered into a buffer and turned into a single type step, with the actual text rebuilt from the raw keys, including honoring backspaces so corrections come out as the final text. Repeated clicks on the same element collapse into one. Control-key auto-repeat, like the eighty Ctrl presses a single held key produced, simply disappears because it contributes nothing to the text.

The second stage handles credentials. When the typing went into a field whose name looks like a password field, the plan does not store what was typed. Instead it stores a reference like [SECRET: Password], so the plan records that a password goes here without ever keeping the password itself in plaintext. The real value would be pulled from a secure store at replay time, never from the plan file. This keeps the plan safe to save, edit, and share.

The third stage labels each step with a readable instruction, and this is where the core architectural bet pays off visibly. If the element already has a name from the accessibility tree, which it captured back in Phase 1, labeling is instant and free: "Click the Submit button", "Select Virginia", "Type Fairfax". Only when the name is empty does the step get handed to the Phase 2 vision model. On a full real recording, about 86 percent of steps were labeled straight from the tree with no model at all, and only about 14 percent would ever need vision. That number is the whole thesis of the project in one statistic.

The fourth stage writes the result in two forms of the same workflow. plan.txt is the human-readable version, a numbered list of plain instructions you review and edit like a document. plan.json is the structured version, carrying the action, the element details, and the secret references, which is what the replay engine will read to actually execute each step.

**What a real distillation looks like.** I recorded a complete job application on a Workday portal, start to finish: opening Overleaf, pasting and editing a resume, downloading and renaming it, then filling the entire multi-step application form with address, education, and demographic fields, and submitting. That produced 640 raw events. Distillation turned it into 174 readable steps, with the typed fields reconstructed correctly, the passwords masked, and the vast majority labeled straight from the accessibility tree. The resulting plan reads like a recipe of the whole application.

---

## Roadmap

Phases 1 through 3 are done: the agent can record, understand, and plan. What remains:

- **Phase 4 - Replay engine**: a state machine (LangGraph) that executes the plan one step at a time, finds each target with a self-healing locator that tries semantic methods first and vision last, highlights it with an on-screen overlay, waits for your approval, acts, and verifies the screen actually changed before moving on.
- **Phase 5 - Correction memory**: pause, give plain-language feedback, the plan step is patched and the correction is saved into a local vector store so the lesson is remembered on future runs.
- **Phase 6 - MCP server**: expose learned workflows as tools other agents can call.
- **Phase 7 - Evaluation and packaging**: a benchmark for success rate and human interventions, plus a self-bootstrapping installer with hardware-aware model selection.

## Tech stack

In use today: Python 3.11, pynput, pywinauto on the UIA backend, mss, SQLite in WAL mode, Ollama running a quantized Qwen3-VL, and Pillow for image preparation. Coming in later phases: LangGraph for the replay state machine, Playwright over the Chrome DevTools Protocol for browser control, sqlite-vec with nomic-embed-text for correction memory, PyQt6 for the on-screen overlay, the MCP SDK, and self-hosted Langfuse for tracing.

## Running what exists

```bash
# 1. record a workflow (press Esc to stop)
pip install pynput pywinauto mss
python mini_recorder.py

# 2. try local vision on a captured screenshot (needs Ollama + qwen3-vl:2b)
pip install ollama pillow
python grounding_test.py

# 3. distill the recording into a readable, editable plan
python distill.py     # writes plan.txt and plan.json
```

A word of caution. The recorder captures screenshots and interface text of whatever is on your screen while it runs, and the distilled plan can contain personal details from the workflow you recorded. So recording.db, the captures folder, and the plan files are all treated as private, excluded from version control by the gitignore, and never leave your machine.