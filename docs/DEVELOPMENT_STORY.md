# Local models, messy invoices, and a desktop app

I wanted to learn two things: how to run inference on my own machine with open
models, and how to build a desktop app instead of another website. I also like
things tidy, and invoice filenames tend to look like someone lost a fight with a
random-number generator. That gave me a project combining all three.

The idea was simple: read an invoice, suggest a useful filename, let me check
it, and rename the file. A little housekeeping with a language model attached.

I had already been using AI-assisted development for a while. The new territory
was putting a model *inside the product’s local workflow*: downloading it,
loading it, getting useful answers out of it, and persuading it to release the
memory afterward.

I wanted something I could keep using after leaving the development project
alone. Once the app is built and the model downloaded, I can open it and work
offline. The app starts its own local worker in the background. No development
server to start, no environment to reconstruct before tidying a few invoices. I
wanted opening the tool to feel like opening an app, without first becoming the
system administrator of my previous weekend’s work.

The [README](../README.md) has the current setup and supported features. Here, I
want to explain how I got there, including the bits that needed a second
attempt.

## A small job with an unexpectedly large specification

The filename I wanted has a straightforward shape:

`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`

It should tell me when the invoice was issued, who sold something, what it was,
and how much it cost. Finding that information is where the apparently simple
task starts growing appendices.

An invoice can contain several dates, several companies, several totals, and
several products. The payment provider is not necessarily the seller. The net
amount is not the gross amount. A document containing ten line items does not
necessarily have one obvious product description.

I kept the final formatting deterministic: transliterate German umlauts,
normalize the filename segments, round the amount to whole currency units, and
retain the currency code. The review view keeps the precise extracted amount. A
missing field becomes a visible gap and warning rather than an invitation to
improvise. There is plenty of creativity involved in building software; the
invoice total is a poor place to express it.

That boundary needed a correction of its own. Initially, punctuation from model
output survived into filenames because the sanitizer only removed characters
that the operating system prohibited. I tightened it to the allowed letters,
digits, hyphens, and underscores. Interpreting an invoice is a useful task for a
model. Enforcing a list of allowed characters is a function’s job description.

## Claude writes, Codex reviews, and I still have homework

My usual setup is Claude Code for coding and GPT Codex for review. I work with
either or both to develop a plan with dedicated blocks, then tackle one block at
a time. After implementation, I check the code myself, build and test it live
where that makes sense, and ask Codex for a sanity check.

A plan for the whole feature provides direction; a completed block gives me code
and behavior I can actually inspect. It also gives the reviewer a more focused
question than “here is an entire application, please detect all remaining
problems in the universe.”

I do my own research for architecture decisions and compare models on real
invoices. The assistants take substantial implementation and review work, but I
still need to decide what I want and try the result on the machine where it is
supposed to work.

The [repository instructions](../AGENTS.md) capture that working loop. The
implementation plans preserve decisions, checks, and unresolved limits. Those
records are also how this story can include specific mistakes instead of relying
on a suspiciously flawless memory of the project.

Using two assistants does not turn their agreement into proof. The XML work
later provided a useful example: automated checks passed, then my manual tests
found more problems.

## A filename needs three languages, apparently

I started with React and TypeScript for the interface, Tauri and Rust for the
desktop shell, and a local Python/FastAPI worker for document processing and
inference. I built out a complete path through model downloads, queued analysis,
review and approval, native renaming, and Undo.

The model suggests invoice fields. Ordinary code builds the filename, and
renaming requires approval. The model has no filesystem privileges. That
boundary stayed useful even when I later replaced the inference runtime.

I initially planned to add frontier cloud models and test against them too. But
the local models worked well enough for the task, so I dropped that part of the
plan.

Analysis runs through one serialized worker thread, keeping model use and
loading under one owner while the interface shows queued and running jobs. Model
installation became a separate responsibility: resumable downloads, SHA-256
verification, hardware compatibility checks, and removal through the app. Before
I could ask a model about an invoice, I had to build the machinery that gets the
right bytes onto disk.

## First, get something readable out of the document

The processing pipeline separates reading the document from understanding it.
For a PDF with useful text, the reader extracts that text directly. A page with
fewer than 20 non-whitespace characters goes through rendering and OCR. That
decision happens per page, so a mixed PDF does not have to be treated as either
entirely scanned or entirely digital.

OCR started with Tesseract and German/English language data. On the Mac, that
meant either asking users to install Tesseract or bundling its executable,
libraries, and language data with the app. I switched the Mac build to Apple
Vision, the native macOS OCR framework, and kept Tesseract on Linux. One less
engine to ship.

The language model receives the resulting text; this is not a vision model
looking directly at invoice images. That distinction matters when diagnosing
mistakes. If OCR has already garbled a label or number, the extraction prompt is
working from damaged input. Adding another sentence to the prompt does not
restore missing pixels.

While testing, I found a handful of old invoices in my own folders as JPEGs
instead of PDFs. I hadn’t planned to support them, but the OCR step already
worked with pixels from rendered PDF pages. JPEG support mostly meant removing
assumptions that every input was a PDF and preserving the file’s actual
extension when renaming it.

## Fast answers are nice. Correct decimal points are nicer

I couldn’t just pick a model by reputation, so I ran a small set of my own real
invoices through several candidates and compared the output field by field. The
[results](model_benchmark_findings.md) were not subtle: some models were fast
but casually wrong, producing a country code where a currency belonged, a
missing decimal point, or a date that wasn’t a date. Others got the fields right
but took so long that the filename suggestion arrived well after I’d have
renamed the file myself.

Granite came out as the accuracy default. Smaller, faster models got a turn as
the quick option until a later engine swap made Granite fast enough that the
tradeoff stopped mattering for my use.

These were decisions based on my invoices and my machine, rather than a general
ranking of the models. Reading the source documents and model answers side by
side was more useful than choosing from benchmark reputations.

## The best inference call sometimes has zero tokens

Some PDFs carry structured invoice data as embedded XML through
ZUGFeRD/Factur-X. I added support for reading it directly. When the XML has
everything I need, the app skips OCR and the model entirely. When it is
incomplete, the model only fills the gaps.

Getting there took a few rounds of correction, documented in the [XML
implementation record](plans_done/05_zugferd-extraction.md). Duplicate
attachments and totals could lead to the wrong value being selected. A single
bad field could sink an otherwise good model response. A weak retry prompt
sometimes just repeated the same mistake.

Real invoices exposed cases my initial fixtures hadn’t represented. XML that
parsed cleanly in isolation wasn’t enough to establish that the app would choose
the right attachment, preserve valid fields, or recover from a failed fallback.

The resulting integration tests include a PDF whose visible text disagrees with
its embedded XML, and a model response that disagrees with fields already
supplied by partial XML. Those tests check that the source-precedence rule
survives the whole workflow. Complete XML also has to result in zero model
loads: skipping inference should mean leaving the model alone.

## Making the review screen useful at invoice number twenty

The first review screen worked, in the sense that import, analyze, review, and
approve all functioned. What it didn’t do well was tell me the truth about what
had happened. A banner claimed fields were missing when they were really just
flagged with a warning. “Needs review” sounded like a verdict rather than a
queue.

I corrected those messages and surfaced the timings, OCR page counts, and token
usage the backend was already calculating. That made it easier to understand
both the results and the wait.

Re-running a cancelled item initially meant removing and re-importing it. Adding
a Re-Run button also exposed a subtler problem: a late response from an older
run could overwrite a newer one. The UI was tracking which invoice a result
belonged to, but not which run.

The rest was about working through more than a handful of invoices: compact
rows, a warnings/errors filter, a collapsible model panel, and a progress bar
that showed something better than a single spinner. At twenty rows, knowing what
is queued, finished, or worth inspecting becomes part of the basic workflow.

## Then I had to ship the Python

My normal development loop is a Linux container on my Mac, and for websites
that’s fast: restart a server, try the change. This project added a real
build-and-wait cycle, especially once backend changes meant rebuilding the
Python sidecar before I could try anything in the app.

The worker originally unpacked its dependencies on every launch. Switching to a
directory bundle cut startup time substantially, at the cost of roughly three
times the disk space. The [packaging
notes](plans_done/04_worker-startup-packaging.md) have the measurements.

Getting that bundle right also meant fixing a signing bug: without a configured
identity, the app silently never got re-signed, so macOS called it damaged. I
also had to make sure the worker process died with the app instead of surviving
it.

The container setup needed its own repair. Sharing one checkout between Linux
and macOS meant Linux builds were overwriting the Mac’s platform-specific
dependencies and worker binary. The [isolation
checks](plans_done/11_linux-macos-build-isolation.md) record the fix: separate
build environments, mirrored outside the shared mount.

The slower development loop is the part of desktop development I could do
without. Being able to close that development setup and still use the finished
app is the payoff.

## The model is downloaded, selected, loaded… which one do you mean?

These states sound interchangeable until several gigabytes of model memory are
involved. Early on, loading the selected model introduced a silent delay. Once
loaded, it stayed resident for the lifetime of the application unless another
model replaced it. There was no separate action for giving the memory back.

I added live memory reporting, an explicit loading/loaded state, and unload
controls. Selecting a model remains separate from whether its weights are
currently resident. Unloading does not mean uninstalling it or forgetting the
selection; the next analysis can load it again.

The [lifecycle work](plans_done/10_model-lifecycle-and-memory-status.md) added
automatic unloading after five minutes idle and an optional unload-after-batch
control. The idle clock starts after work finishes. A long inference call is
busy, even if the user has had time to become extremely idle themselves.

## Swap the engine, then find everything attached to it

To cut memory use and speed up inference, I replaced Transformers/PyTorch with
llama.cpp and quantized GGUF weights, using one runtime for both macOS and
Linux.

The work went beyond changing the inference call. Tokenization, chat templates,
and token handling had to be right. The model also needed enough context to
receive a real invoice’s extraction prompt without silent truncation. The
[migration record](plans_done/12_llamacpp-inference-runtime.md) has the
specifics.

In a local comparison, the swap roughly halved Granite’s per-invoice time. That
was enough for the smaller, faster-but-shakier models to stop being worth their
accuracy tradeoff for me. Granite stayed the default, while the rest of the
lineup continued to change with licensing and benchmark results.

The migration also left loose ends: a GPU indicator still wired to the old
runtime stopped reporting anything, and a packaging step kept pulling in a
PyTorch dependency the app no longer used. Removing a dependency from the code
and removing it from the shipped application turned out to be separate jobs.

I verified the full migration workflow on my Mac. Linux GPU checks are still
open in the migration notes.

## Trust, but inspect the proposed filename

The app now has backend tests for parsing, validation, routing, and API
behavior; frontend tests for workflow state; and Rust tests for renaming and
history. Synthetic fixtures make cases repeatable without publishing my private
invoices. Real-model runs and native app checks cover things a mocked model
cannot tell me. A fake model is wonderfully obedient. That is also its main
limitation.

The file operations have their own defenses: explicit approval, refusal to
overwrite existing files, and Undo records that check file identity. Those
checks protect the rename operation while I review whether the extracted date,
seller, product, and amount are right.

Undo also has to cope with the world changing after a rename. The original name
might now be occupied, or the file might have been moved or replaced. A matching
path alone does not establish that it is still the same file.

Batch history survives restarts, while Redo covers the latest undo in the
current session. Since a rename batch can partially succeed, the UI reports
results per file.

These details are where the desktop-app goal becomes tangible. I am operating on
files that already exist in someone’s folders. The model’s output needs review,
and the code applying it needs independent checks. A confident sentence from an
AI is not an overwrite policy.

## Now it gets to meet the rest of my invoices

I have started working through my personal invoices with the app, and the
experience so far is good. After the model comparisons, packaging fixes, and
environment repairs, I am using it for the thing I built it to do: tidying
invoice filenames. The PDFs have finally become the main task again.

This is also how I usually develop. I use what I have built, encounter an issue
or notice something that would make it better, then fix it or add the feature. A
plan gets a change built; living with the result supplies the next set of ideas.

The app is already doing a job for me. I can leave the development environment
closed, open the app, and rename invoices. And if using it reveals the next
annoying detail, that becomes the next block of work.