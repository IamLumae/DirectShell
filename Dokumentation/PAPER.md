# DirectShell: Universal Application Control Through the Accessibility Layer

**A Technical Whitepaper**

Martin Gehrken
February 2026

---

## Abstract

The current paradigm for AI-driven desktop automation relies on screen capture and visual inference — a process that is expensive, slow, context-consuming, and fragile. This paper introduces DirectShell, a system that bypasses the visual layer entirely by transforming the operating system's accessibility infrastructure into a universal, structured, queryable interface to any graphical application. DirectShell continuously captures the complete semantic state of any target application — every element, its role, state, value, and position — and makes it available as structured, machine-readable data. An action queue enables external processes to operate the target application through the same interface — all without screenshots, without application-specific APIs, and without the cooperation of the software vendor.

The accessibility infrastructure exists in every modern operating system and cannot be removed or restricted without violating disability rights legislation in virtually every jurisdiction worldwide. This creates a legally protected, universal interface to the graphical frontend of every application — a category of tool that did not previously exist.

---

## 1. The Problem: The Screenshot Bottleneck

### 1.1 The State of AI Desktop Automation in 2026

Every major AI laboratory is pursuing the same objective: autonomous agents that operate desktop software. OpenAI's Operator, Anthropic's Computer Use, Google's Project Mariner, and Microsoft's Copilot Actions all attempt to enable language models to use graphical applications the way a human employee would — reading the screen, understanding the interface, and performing actions.

All of them use the same fundamental approach: **screen capture and visual inference**.

The process is:

1. Capture a screenshot of the application
2. Send the image to a vision-language model
3. The model interprets the image, identifies UI elements, and determines where to click
4. Coordinates are sent back to a control layer that executes the click
5. A new screenshot is captured to observe the result
6. Repeat

### 1.2 Why Screenshot-Based Automation Fails

This approach has five structural weaknesses that cannot be resolved within the paradigm:

**Cost.** A single screenshot at 1920x1080 resolution consumes approximately 1,200–1,800 tokens when encoded for a vision-language model. A multi-step workflow requiring 20 interactions consumes 24,000–36,000 tokens in image data alone — before the model performs any reasoning. At current API pricing, this makes even simple automation workflows expensive at scale.

**Context saturation.** Language models have finite context windows. Every screenshot injected into the context displaces space that could be used for reasoning, instructions, or memory. An agent operating across multiple applications accumulates screenshots rapidly, degrading the model's ability to maintain coherent multi-step plans. This is the "stuffed head" problem: the agent becomes progressively less capable as the task grows more complex, not because the task is harder, but because visual data is consuming its working memory.

**Latency.** Each action requires a round trip: capture, encode, transmit, process, respond, execute. At typical API latencies, this introduces 2–5 seconds per action. A 30-step workflow takes 1–2.5 minutes even when every step succeeds on the first attempt.

**Fragility.** Visual inference is resolution-dependent, theme-dependent, font-dependent, and language-dependent. A model trained to recognize a "Save" button at 100% scaling may fail at 125%. Dark mode changes the visual fingerprint of every element. Localized interfaces present the same UI in different languages. Every screenshot is a lossy, ambiguous representation of the underlying interface state.

**Opacity.** A screenshot contains pixels. It does not contain semantics. The model cannot distinguish between a button labeled "Delete" and an image that happens to contain the word "Delete." It cannot determine whether a text field is editable, disabled, or read-only. It cannot identify off-screen elements, scroll positions, or hierarchical relationships between UI components. The model is inferring structure from visual patterns — it is never actually reading the interface.

### 1.3 The Missing Layer

Desktop applications already describe themselves in full structural detail. Every button declares its name, role, enabled state, and position. Every text field exposes its current value. Every menu hierarchy is represented as a traversable tree. This self-description exists in every application on every modern operating system, maintained in real-time, updated on every UI change.

This data was built for screen readers — assistive technology that enables blind and visually impaired users to operate computers. It has existed in Windows since 2005 (UI Automation) and in predecessor form (MSAA) since 1997.

No major AI agent framework uses it.

---

## 2. The Accessibility Layer

### 2.1 A Structured Description of Every Application

Every modern operating system maintains a parallel, non-visual representation of every graphical application. This representation was originally designed for screen readers — software that enables blind and visually impaired users to navigate computers. In this representation, every button, text field, menu, list, and content element is described by its semantic properties: what it is (role), what it's called (name), what it contains (value), where it is (position), and whether it can be interacted with (state).

This is not an optional feature. It is a platform-level requirement, enforced by the operating system and mandated by law. Every major application framework — native, web-based, and cross-platform — implements it. Chromium-based applications (Chrome, Edge, Opera, Electron) build this representation when assistive technology signals its presence. The coverage is a platform invariant.

### 2.2 The Gap: What Nobody Built

Accessibility APIs have been used for four purposes:

1. **Screen readers** (JAWS, NVDA, Narrator) — read elements aloud for blind users
2. **Automation frameworks** (UI Automation client libraries, Accessibility Insights) — developer testing tools
3. **RPA tools** (UiPath, Automation Anywhere) — use accessibility selectors as one of several element-targeting strategies, alongside image matching and coordinate-based clicking
4. **Research** (academic papers on accessibility testing and UI understanding)

What **did not exist** before DirectShell:

- A system that dumps the complete accessibility tree into a **queryable database** at continuous refresh rates
- A system that generates multiple **machine-readable output formats** designed for consumption by language models
- A system that provides a **universal action queue** where any external process can submit input actions by element name
- A system designed from the ground up as a **universal interface layer** between AI agents and arbitrary graphical applications

RPA tools use accessibility APIs as one targeting mechanism among many. They require per-application scripting. They do not expose the full element tree as a queryable data structure. They are workflow automation tools, not universal interface layers.

---

## 3. DirectShell

### 3.1 Design Principle

DirectShell operates on one premise: **the accessibility tree is the interface.**

Instead of capturing what an application looks like (pixels), DirectShell captures what an application **is** (structure). Instead of asking "where should I click?" (visual inference), an external agent asks "what elements exist and what can I do with them?" (structured query).

### 3.2 Operation

DirectShell is a lightweight native overlay (single binary, no runtime dependencies) that attaches to any target window. Once attached, it performs two continuous operations:

**Perception:** DirectShell continuously captures the complete semantic state of the target application — every element, its role, name, value, position, enabled/disabled state, and visibility — and exposes it as structured, instantly queryable data. The refresh cycle runs multiple times per second.

**Action:** External processes submit actions (type text, press keys, click elements, scroll) through a standardized queue. DirectShell executes these actions deterministically against the target application using the operating system's native interaction mechanisms — not simulated mouse movements to pixel coordinates, but semantic operations on identified elements.

### 3.3 The Universal Output: What Every Application Looks Like to an AI

This is the core of DirectShell's value proposition. Regardless of the target application — whether it is a browser, an ERP system, an email client, or a 20-year-old legacy application — DirectShell produces the same structured output format.

Here is what Google Gemini running in Opera looks like through DirectShell:

```
# opera.a11y.snap — Operable Elements (DirectShell)
# Window: Google Gemini – Opera

[1] [keyboard] "Adressfeld" @ 168,41 (2049x29)
[2] [click] "Neuer Chat" @ 45,200 (200x30)
[3] [click] "Meine Inhalte" @ 45,240 (200x30)
[4] [click] "Gems" @ 45,280 (200x30)
[5] [keyboard] "Einen Prompt für Gemini eingeben" @ 999,1177 (1069x37)
[6] [click] "Einstellungen & Hilfe" @ 1800,1350 (150x20)

# 6 operable elements in viewport
```

Every application on the planet, reduced to the same format: **what can be operated, what type of input it accepts, what it's called, where it is.** No screenshots. No pixels. No ambiguity. A text file that any language model — or any script, or any program — can read and act on.

An AI agent does not need to "see" the screen. It reads this file and knows: there are 6 things I can interact with. Element 5 is a text input called "Einen Prompt für Gemini eingeben." I can type into it. That is the entire perception step. No vision model. No token-heavy image encoding. A few lines of text.

The same output format works for SAP, for Outlook, for Excel, for any application that runs on the operating system. The application does not need to cooperate. It does not need an API. It does not need to know DirectShell exists.

### 3.4 Resource Comparison

| Metric | Screenshot-Based Agent | DirectShell |
|--------|----------------------|-------------|
| Per-action data size | 1,200–1,800 tokens (image) | 50–200 tokens (text) |
| Element identification | Visual inference (probabilistic) | Name-based lookup (deterministic) |
| Action targeting | Pixel coordinates (fragile) | Element name (semantic) |
| Refresh rate | On-demand (seconds) | Continuous (500ms) |
| Resolution dependency | Yes | No |
| Theme dependency | Yes | No |
| Language dependency | Yes (visual) | Partial (element names are localized, but structured) |
| Off-screen element access | No | Yes (via database query) |
| Element state (enabled/disabled) | Inferred from appearance | Explicitly reported |
| Hierarchical structure | Lost in flattening | Preserved (parent_id tree) |
| Multi-element queries | Not possible | SQL queries in milliseconds |
| Context window impact | High (images fill context) | Low (structured text is compact) |

The token efficiency difference is approximately **10:1 to 30:1** depending on the complexity of the target interface. For continuous background monitoring tasks, the difference exceeds **100:1**. This means an agent using DirectShell can maintain 10–30x more operational history in its context window, enabling significantly longer and more complex workflows without context degradation.

---

## 4. Why This Cannot Be Blocked

### 4.1 The Legal Framework

The accessibility interface that DirectShell uses is protected by an interlocking network of international, regional, and national legislation:

**International:**
- **UN Convention on the Rights of Persons with Disabilities (CRPD)** — Article 9 (Accessibility), Article 21 (Freedom of expression and access to information). Ratified by 186 states.

**European Union:**
- **European Accessibility Act (EAA)** — Directive (EU) 2019/882. Requires all consumer-facing digital products and services to be accessible. Enforcement begins June 2025. Member states must transpose into national law.
- **Web Accessibility Directive** — Directive (EU) 2016/2102. Requires public sector digital services to meet WCAG 2.1 Level AA, which explicitly requires programmatic accessibility (Success Criterion 4.1.2: Name, Role, Value).
- **EU Charter of Fundamental Rights** — Article 26 (Integration of persons with disabilities).

**United States:**
- **Americans with Disabilities Act (ADA)** — Title III has been interpreted by courts to apply to software and digital services.
- **Section 508 of the Rehabilitation Act** — Requires federal agencies to procure accessible ICT. Explicitly references WCAG and programmatic accessibility.
- **21st Century Communications and Video Accessibility Act (CVAA)** — Requires accessibility in advanced communications services and equipment.

**Germany (specifically):**
- **Barrierefreiheitsstärkungsgesetz (BFSG)** — German transposition of the EAA. In force since June 2025. Requires digital products to support assistive technology.
- **Behindertengleichstellungsgesetz (BGG)** — Federal disability equality law.
- **Grundgesetz Article 3(3)** — Constitutional prohibition of disability discrimination.

### 4.2 What This Means in Practice

The Windows UI Automation framework exists **because the law requires it to exist.** Applications must expose their interface elements programmatically so that screen readers and other assistive technology can access them. A software vendor who disables, restricts, or degrades their UIA implementation risks:

1. Non-compliance with the European Accessibility Act (fines set by member states)
2. Exclusion from government procurement under Section 508
3. ADA lawsuits in the United States (established precedent in digital accessibility cases)
4. Violation of the UN CRPD in 186 signatory states

**DirectShell does not exploit a vulnerability.** It does not reverse-engineer proprietary protocols. It does not bypass security mechanisms. It reads an interface that the operating system explicitly publishes for third-party assistive technology to consume. The fact that DirectShell is not a screen reader is immaterial — the law mandates that the interface must be available, not that only specific categories of software may use it.

### 4.3 The Unpatchability Argument

Consider the response options available to a software vendor who wishes to prevent DirectShell from reading their application:

| Response | Effect | Legal Consequence |
|----------|--------|-------------------|
| Disable UIA tree | Blocks DirectShell | Violates EAA, Section 508, ADA. Excludes blind users. |
| Return empty/minimal UIA data | Partially blocks DirectShell | Violates WCAG 4.1.2 (Name, Role, Value). Degrades screen reader experience. |
| Detect and block UIA clients | Blocks DirectShell | Also blocks JAWS, NVDA, and Narrator. Discrimination against disabled users. |
| Encrypt UI element names | Blocks DirectShell | Makes screen readers unable to read the interface. Accessibility violation. |
| Remove meaningful element names | Partially blocks DirectShell | Violates WCAG accessibility requirements. |
| Kernel-level anti-cheat (block input injection) | Blocks action injection only | Does not block reading. Also blocks legitimate assistive input devices. |

Every countermeasure that blocks DirectShell's read capability also blocks screen readers. There is no technical mechanism to distinguish between a screen reader querying the accessibility layer and DirectShell querying the accessibility layer — both use the same operating system interfaces, the same traversal methods, the same property queries. The operating system does not authenticate accessibility clients.

This creates a permanent, legally guaranteed read capability against every application that runs on the platform. The only exception is applications that legitimately have no UI elements (command-line tools, background services), which have no UIA tree to read in the first place.

---

## 5. Implications

### 5.1 For AI Agents

DirectShell converts the problem of "computer use" from a vision task to a text task.

A language model operating through DirectShell does not need vision capabilities. It reads a structured text file describing the screen state, selects an action, and writes it to a database. The entire perception-action loop is text-in, text-out — the native operating mode of every language model.

This means:
- **Any language model can operate any application.** Not only multimodal models with vision. GPT, Claude, Gemini, Llama, Mistral, DeepSeek — any model that can read text and produce structured output can drive a desktop application through DirectShell.
- **Context efficiency enables complex workflows.** Where a screenshot-based agent runs out of context after 10–20 actions, a DirectShell-based agent can maintain hundreds of actions in its context window. This enables multi-application workflows, long-running processes, and recovery from errors without losing operational history.
- **Deterministic targeting eliminates ambiguity.** "Click the element named 'Save'" is unambiguous. "Click the button that looks like it says Save at approximately pixel (1420, 780)" is not. DirectShell removes the class of failures caused by visual misidentification.

### 5.2 For Proprietary APIs

The enterprise software industry derives significant revenue from controlling access to application data through proprietary APIs. SAP, Salesforce, Oracle, ServiceNow, and hundreds of other vendors charge for API access — often per-user, per-month, on top of the base license. The business model is: your data lives in our application, and you pay us for the privilege of accessing it programmatically.

DirectShell offers an alternative path. Any data visible in the application's user interface is accessible through the UIA tree. If a field is displayed on screen, its name and value are in the element tree. If a table is rendered, its rows and columns are traversable. The data does not need to be extracted through the vendor's API — it is already published through the accessibility interface.

This does not replicate full API functionality. It does not provide bulk data export, webhook-based event triggers, or server-side query optimization. What it provides is **universal read access to any data the application displays to the user,** and **universal write access to any input the application accepts from the user.** For many automation use cases — filling forms, extracting displayed data, navigating workflows, operating applications — this is sufficient.

The structural shift: the accessibility layer turns every GUI application into an application with an open, standardized, non-proprietary interface. Not through the vendor's cooperation. Through the operating system's mandate.

### 5.3 For the Software Industry

The implications extend beyond API revenue:

**Anti-cheat systems.** Online games invest heavily in preventing automated input. DirectShell's action queue enables programmatic control of any application, including games. Kernel-level anti-cheat can detect and block SendInput calls, but cannot block reading the UIA tree without simultaneously blocking assistive technology. The read capability — knowing every element on screen, every health bar value, every minimap position — is arguably more disruptive than the write capability.

**Terms of Service enforcement.** Many applications prohibit automated access in their TOS. The legal enforceability of such terms against a tool that uses a legally mandated accessibility interface is untested. The conflict between "our TOS says you can't automate" and "the law says you must provide this interface" creates legal uncertainty that favors the user, not the vendor.

**DRM and content protection.** Applications that display protected content (e-books, streaming subtitles, licensed data) expose that content through the UIA tree if it is rendered as accessible text. The accessibility requirement creates a structured, text-based output channel for content that may otherwise be protected against copying.

**Quality assurance and compliance.** On the constructive side, DirectShell enables automated testing of any application without access to its source code. Regulatory compliance verification (e.g., checking that a financial application displays required disclosures) can be performed programmatically against the production UI.

### 5.4 For Cross-Platform Potential

While DirectShell currently targets Windows UIA, equivalent accessibility frameworks exist on every major platform:

| Platform | Framework | Coverage |
|----------|-----------|----------|
| Windows | UI Automation (UIA) | All Win32, WPF, WinForms, UWP, WinUI, Chromium, Electron |
| macOS | NSAccessibility / AXUIElement | All Cocoa, Carbon, Chromium, Electron |
| Linux | AT-SPI2 (Assistive Technology SPI) | GTK, Qt, Chromium, Electron |
| Android | AccessibilityService API | All applications |
| iOS | UIAccessibility | All UIKit, SwiftUI |

The architectural pattern — attach to application, walk accessibility tree, store in database, expose action queue — is transferable to any platform. The legal protections are similarly cross-platform: the EAA covers all digital products in the EU regardless of operating system, and the ADA applies to digital services regardless of platform.

---

## 6. Limitations and Honest Assessment

### 6.1 Accessibility Implementation Quality

The UIA tree is only as informative as the application's accessibility implementation. Applications with poor accessibility practices may have:
- Unnamed elements (buttons without labels)
- Missing roles (custom controls reported as "Custom" instead of their functional role)
- Absent values (text fields that don't expose their content programmatically)
- Flat hierarchies (no meaningful parent-child relationships)

In practice, major applications (Microsoft Office, browsers, SAP GUI, enterprise software subject to Section 508 requirements) have comprehensive accessibility implementations. Smaller or legacy applications may have gaps. The quality of DirectShell's output directly correlates with the quality of the target application's accessibility support.

### 6.2 Dynamic and Canvas-Based Content

Applications that render content to a canvas (games, design tools, PDF viewers, map applications) may expose limited accessibility data for the rendered content. A game rendering a 3D scene does not describe every visual element in the UIA tree. A drawing application may expose the canvas as a single element rather than individual shapes.

Web applications rendered in browsers are an important exception: Chromium-based browsers expose the full DOM accessibility tree, meaning web content is fully readable through UIA even when it uses complex rendering.

### 6.3 Write-Side Limitations

Kernel-level anti-cheat systems (Riot Vanguard, Easy Anti-Cheat, BattlEye) can detect and block certain forms of programmatic input injection. This may affect DirectShell's action capabilities but not its read capability. The distinction is important: the read pathway operates through the accessibility framework at a higher privilege level than the write pathway and cannot be blocked without affecting assistive technology.

### 6.4 Single-Application Scope

DirectShell v0.2.0 attaches to one target application at a time. Multi-application workflows require re-snapping between applications. Window switching automation is not yet implemented. This is an engineering limitation, not an architectural one — the system is designed to extend to multi-window operation.

### 6.5 Performance Boundaries

A full accessibility tree traversal of a complex application (browser with many tabs, IDE with large project) can take 200–800ms. DirectShell's streaming architecture ensures partial data is available during traversal, but extremely complex interfaces may experience slight lag in the refresh cycle.

---

## 7. Positioning: What DirectShell Is

DirectShell is not an automation script. It is not an RPA tool. It is not a screen reader.

DirectShell is a **universal interface layer** between any programmatic agent and any graphical application.

Before DirectShell, the graphical frontend of every application was a closed system. You could look at it (screenshots) or you could use the vendor's API (if one existed, if you could afford it, if the vendor allowed it). There was no general-purpose, structured, queryable, writable interface to the visual layer of software.

After DirectShell, every application that has a window has a universal interface. The same structured output. The same action format. The same data model. Regardless of vendor, language, age, or platform.

The closest historical parallel is the web browser. Before browsers, every networked information system had its own client, its own protocol, its own access method. The browser provided a universal client for all of them. DirectShell provides a universal client for all graphical applications — with the additional property that it cannot be blocked without violating international disability rights law.

The architectural contribution is the recognition that the accessibility layer — maintained by law, implemented by every application, exposing full structural and semantic information — is a universal API that nobody was using as one.

---

## 8. Timeline

- **1997:** Microsoft Active Accessibility (MSAA) introduced in Windows 95/98
- **2005:** UI Automation framework introduced in Windows Vista
- **2019:** European Accessibility Act adopted (EU 2019/882)
- **2023–2025:** OpenAI, Anthropic, and Google launch screenshot-based computer use agents
- **2025:** European Accessibility Act enforcement begins (June 28, 2025)
- **February 16, 2026:** DirectShell v0.2.0 — first successful multi-turn conversation between an AI agent and a GUI application through the accessibility layer, without screenshots

---

## 9. Conclusion

The AI industry's current approach to desktop automation — screenshot capture and visual inference — is a workaround for a problem that was already solved. The accessibility layer provides everything that screenshots provide and more: structure, semantics, state, hierarchy, queryability. It provides it faster (milliseconds vs. seconds), cheaper (text vs. images), more reliably (deterministic lookup vs. probabilistic inference), and more efficiently (10–30x fewer tokens per interaction).

DirectShell makes this layer usable as a universal application interface. It requires no cooperation from software vendors. It works with every application on the platform. And it is protected by the same laws that protect the right of disabled people to use computers — laws that exist in virtually every jurisdiction on Earth and that no software vendor can circumvent without facing legal consequences.

The technology described in this paper was built in a single session by one developer and one AI agent. The reference implementation is a single compact binary with no external dependencies. The implications extend to every application, every operating system, and every business model that depends on controlling access to graphical interfaces.

---

*DirectShell v0.2.0*
*Martin Gehrken, 2026*
*Contact: thelastrag.de*
