# Local models, messy invoices, and a desktop app

I wanted to learn two things: how to run inference on my own machine with open
models, and how to build a desktop app instead of another website. I also like
things tidy, and invoice filenames tend to look like someone lost a fight with a
random-number generator. That gave me a project combining all three.

The idea was simple: read an invoice, suggest a useful filename, let me check it,
and rename the file. A little housekeeping with a language model attached.

I had already been using AI-assisted development for a while. The new territory
was putting a model *inside the product's local workflow*: downloading it,
loading it, getting useful answers out of it, and persuading it to release the
memory afterward.

I also wanted something I could keep using after leaving the development project
alone. Once the app is built and the model downloaded, I can open it and work
offline. No development server to start, no environment to reconstruct before
tidying a few invoices. The app starts its own local worker in the background.
I wanted opening the tool to feel like opening an app, without first becoming
the system administrator of my previous weekend's work.

The [README](../README.md) has the current setup and supported features. Here,
I want to explain how I got there, including the bits that needed a second attempt.

## Claude writes, Codex reviews, and I still have homework

My usual setup is Claude Code for coding and GPT Codex for review. I work with
either or both to develop a plan with dedicated blocks, then tackle one block
at a time. After implementation, I check the code myself, build and test it live
where that makes sense, and ask Codex for a sanity check.

That gives me something concrete to inspect at each step. A plan for the whole
feature provides direction; a completed block gives me code and behavior I can
actually check. It also gives the reviewer a more focused question than “here
is an entire application, please detect all remaining problems in the universe.”

I do my own research for architecture decisions. I also run the app and compare
models, rather than treating a generated implementation or recommendation as
the final answer. The assistants take substantial implementation and review
work, but I still need to decide what I want, inspect what changed, and try the
result on the machine where it is supposed to work.

The [repository instructions](../AGENTS.md) capture the working rules around
that loop. Changes should stay focused, checks should start with the narrowest
useful tests, and debugging should start with actual errors and responses.
For multi-block features, the plan should include a happy-path test crossing
the blocks and a final security, sanity, and safety review. A collection of
individually plausible pieces still needs to work when connected together.

Those rules also leave staging and committing with me. The changelog records
what changed, and the implementation plans preserve decisions, checks, and
unresolved limits. That is useful context when moving between implementation
and review. It is also how this story can include specific mistakes instead
of relying on a suspiciously flawless memory of the project.

Using two assistants does not turn their agreement into proof. The XML work
later provided a particularly good example: automated checks passed, then my
manual tests found more problems. My quick code review, Codex's sanity check,
automated tests, and live use each look at the result differently. None gets
to replace all the others just because it reports success in a confident tone.

## A small job with an unexpectedly large specification

The filename I wanted has a straightforward shape:
`YYYY-MM-DD_Seller_Product_Amount-CURRENCY.pdf`. It should tell me when the
invoice was issued, who sold something, what it was, and how much it cost.
Finding that information is where the apparently simple task starts growing
appendices.

An invoice can contain several dates, several companies, several totals, and
several products. The payment provider is not necessarily the seller. The net
amount is not the gross amount. A document containing ten line items does not
necessarily have one obvious product description. Even after extraction, I still
need consistent date formatting, safe characters, and a name that fits within
the length limit.

I kept the final formatting deterministic: transliterate German umlauts, normalize
the filename segments, round the amount to whole currency units, and retain the
currency code. The review view keeps the precise extracted amount. A missing
field becomes a visible gap and warning rather than an invitation to improvise.
There is plenty of creativity involved in building software; the invoice total
is a poor place to express it.

That boundary needed a correction of its own. Initially, punctuation from model
output survived into filenames because the sanitizer only removed characters
that the operating system prohibited. I tightened it to the allowed letters,
digits, hyphens, and underscores. For me, that is an important part
of building with AI: knowing where a model helps and where a simple function
does the job more easily and reliably. Interpreting an invoice is a useful task
for a model. Enforcing a list of allowed characters is a function's job description.

## A filename needs three languages, apparently

I started with React and TypeScript for the interface, Tauri and Rust for the
desktop shell, and a local Python/FastAPI worker for document processing and
inference. I built out a complete path through model downloads, queued analysis,
review and approval, native renaming, and Undo.

The model gets to suggest invoice fields. It does not get filesystem privileges
and a cheerful instruction to improvise. Ordinary code builds the filename;
renaming requires approval. That boundary stayed useful even when I later
replaced the inference runtime.

I initially planned to add frontier cloud models and test against them too.
But the local models worked well enough for the task, so I dropped that part
of the plan. The app could do what I needed with inference running on my machine.

Analysis runs through one serialized worker thread. That keeps model use and
loading under one owner while the interface can show queued and running jobs.
Model installation became a separate responsibility: resumable downloads,
SHA-256 verification, hardware compatibility checks, and removal through the
app. Before I could ask a model about an invoice, I had
to build the machinery that gets the right bytes onto disk.

## First, get something readable out of the document

The early processing pipeline separated reading the document from understanding
it. For a PDF with useful text, the reader extracts that
text directly. A page with fewer than 20 non-whitespace characters goes through
rendering and OCR. That decision happens per page, so a mixed PDF does not have
to be treated as either entirely scanned or entirely digital.

OCR started with Tesseract and German/English language data. On the Mac, that
meant either asking users to install Tesseract or bundling its executable,
libraries, and language data with the app. I wanted to avoid that extra setup
and packaging work when macOS already had a native OCR framework available.
So I switched the Mac build to Apple Vision and kept Tesseract on Linux.
One less engine to ship.

The language model receives the resulting text; this is not a vision model
looking directly at invoice images. That distinction matters when diagnosing mistakes. If OCR has already
garbled a label or number, the extraction prompt is working from damaged input.
Adding another sentence to the prompt does not restore missing pixels.

While testing, I ran across a handful of old invoices sitting in my own
folders as JPEGs instead of PDFs. I hadn't planned to support them, but the
OCR step never actually cared whether it started from a PDF page or a photo,
it just needed pixels, and that was already how scanned pages got read. So
JPEG support turned out to be less a new feature than admitting the machinery
for it already existed. It just needed to stop assuming everything was a PDF,
and to give the file back its own extension instead of one borrowed from a
format it was never part of.

## Fast answers are nice. Correct decimal points are nicer

I couldn't just pick a model by reputation, so I ran a small set of my own
real invoices through several candidates and compared the actual output
field by field. The [results](model_benchmark_findings.md) were not subtle:
some models were fast but casually wrong, tripping over basic fields: a
country code where a currency belonged, a missing decimal point, a date
that wasn't a date. Others got the
fields right but took so long that the filename suggestion arrived well
after I'd have renamed the file myself.

Granite came out as the accuracy default; smaller, faster models got a turn
as the quick option until a later engine swap made Granite fast enough that
the tradeoff stopped mattering. Nothing here was decided in the abstract; it
came from reading real invoices and real model answers side by side.

## The best inference call sometimes has zero tokens

Some PDFs carry structured invoice data as embedded XML (ZUGFeRD/Factur-X),
and I added support for reading it directly. When the XML has everything I
need, the app skips OCR and the model entirely; when it's incomplete, the
model only fills the gaps. Asking a model fewer questions turned out to be
one of the more useful features I built.

Getting there took a few rounds of correction, documented in the
[XML implementation record](plans_done/05_zugferd-extraction.md): duplicate
attachments and totals could pick the wrong value, a single bad field could
sink an otherwise good model response, and a weak retry prompt sometimes just
repeated the same mistake. Each needed a real invoice to expose it, not just
XML that parsed cleanly in isolation.

## Making the review screen useful at invoice number twenty

The first version of the review screen worked, in the sense that import,
analyze, review, and approve all functioned. What it didn't do well was tell
me the truth about what had happened. A banner claimed fields were missing
when they were really just flagged with a warning. “Needs review” sounded
like a verdict rather than a queue. Both got fixed, alongside surfacing the
timings, OCR page counts, and token usage the backend was already
calculating and just not showing.

A couple of missing buttons turned out to be hiding real bugs. Re-running a
cancelled item meant removing and re-importing it until I added a Re-Run
button, and underneath that button was a subtler problem: a late response
from an older run could overwrite a newer one, because the UI was only
tracking which invoice a result belonged to, not which run.

The rest was about surviving more than a handful of invoices at once:
compact rows, a warnings/errors filter, a collapsible model panel, and a
progress bar that showed something better than a single spinner. None of it
changes what the screen does. It changes whether I can still tell what's
going on once there are twenty rows on it instead of two.

## Then I had to ship the Python

My normal development loop is a Linux container on my Mac, and for websites
that's fast: restart a server, try the change. This project added a real
build-and-wait cycle, especially once backend changes meant rebuilding the
Python sidecar before I could try anything in the app.

The worker itself needed packaging attention too. It originally unpacked its
dependencies on every launch; switching to a directory bundle cut that
startup time dramatically, at the cost of roughly three times the disk
space. The [packaging notes](plans_done/04_worker-startup-packaging.md) have
the numbers. Getting that bundle right also meant fixing a signing bug where
the app silently never got re-signed without a configured identity, so macOS
just called it damaged, and making sure the worker process died with the app
instead of surviving it.

The container setup needed its own fix partway through. Sharing one checkout
between Linux and macOS meant Linux builds were quietly overwriting the
Mac's platform-specific dependencies and worker binary. Same filesystem,
different kernels underneath, and the tools running there had no way to
tell. The [isolation checks](plans_done/11_linux-macos-build-isolation.md)
record the fix: separate build environments, mirrored outside the shared mount.

The slower development loop is the part of desktop development I could do
without. The result is the part I wanted: I can close the development setup,
leave the project alone, and still open the built app later. No server to
restart, no environment to reconstruct. After setup and a model download, it
doesn't need the internet either.

## The model is downloaded, selected, loaded… which one do you mean?

These states sound interchangeable until several gigabytes of model memory are
involved. Early on, loading the selected model introduced a silent delay, and
the model then stayed resident for the lifetime of the application unless
another model replaced it. There was no separate action for giving the memory
back.

I added live memory reporting, an explicit loading/loaded state, and unload
controls. Selecting a model remains separate from whether
its weights are currently resident. Unloading does not mean uninstalling it or
forgetting the selection; the next analysis can load it again.

The [lifecycle work](plans_done/10_model-lifecycle-and-memory-status.md) added
automatic unloading after five minutes idle and an optional unload-after-batch
control. The idle clock starts after work finishes. A long inference call is
busy, not idle, even if the user has had time to become extremely idle themselves.

## Swap the engine, then find everything attached to it

To cut memory use and speed up inference, I replaced Transformers/PyTorch
with llama.cpp and quantized GGUF weights, one runtime for both macOS and
Linux instead of two. The interesting part wasn't the swap itself; it was
tokenization, getting the chat template and token handling exactly right,
and giving the model enough context that a real invoice's extraction prompt
didn't get silently truncated. The
[migration record](plans_done/12_llamacpp-inference-runtime.md) has the
specifics.

The speed difference was real, roughly halving Granite's per-invoice time in
a local comparison, enough that the smaller, faster-but-shakier models
stopped being worth their accuracy tradeoff. Granite stayed the default; the
rest of the lineup around it kept shifting as licensing and benchmarks changed.

The swap also left a couple of loose ends: a GPU indicator still wired to
the old runtime that quietly stopped reporting anything, and a packaging
step still pulling in a PyTorch dependency the app no longer used. Removing
a dependency from the code and convincing the build that it's actually gone
turned out to be two separate jobs.

## Trust, but inspect the proposed filename

I ended up with backend tests for parsing, validation, routing, and API behavior;
frontend tests for workflow state; and Rust tests for renaming and history.
Synthetic fixtures make cases repeatable without publishing my private invoices.
Real-model runs and native app checks cover things a mocked model cannot tell
me. A fake model is wonderfully obedient. That is also its main limitation.

One useful integration test deliberately makes the visible PDF text disagree
with its embedded XML. Another makes the model disagree with fields already
supplied by partial XML. Those cases check that the source-precedence rule
survives the whole API workflow, rather than merely testing that an XML parser
can locate a tag. Complete XML also has to result in zero model loads. A fast
path that quietly loads a multi-gigabyte model would be taking an interesting
route to doing less work.

The manual XML failures were a reminder to test combinations that the first
fixtures did not represent. Duplicate attachments, duplicate field values, and
fallback exceptions exposed different weaknesses from obviously malformed XML.
The passing tests were evidence about the inputs they exercised. They were not
an exhaustive census of what a PDF could contain.

I verified the full runtime-migration workflow on my Mac; the migration notes
keep the outstanding Linux hardware checks separate. Passing one environment's
checks does not magically test the other one.

The file operations have their own defenses: explicit approval, refusal to
overwrite existing files, and Undo records that check file identity. Persistent
batch history and session Redo extended that recovery workflow. None of
those checks can decide whether the model picked the right invoice date. They
protect the file operations while I review the content.

Undo also has to cope with the world changing after a rename. The original name
might now be occupied, or the file might have been moved or replaced. Checking
file identity matters because a matching path alone does not establish that it
is still the same file. The history survives restarts; Redo covers the latest
undo in the current session. A rename batch can partially succeed, so the UI
reports results per file instead of pretending that every batch is one
indivisible transaction.

These details are where the desktop-app goal becomes tangible. I am operating
on files that already exist in someone's folders. The model's output needs
review, and the code applying it needs its own independent checks. A confident
sentence from an AI is not an overwrite policy.

## Now it gets to meet the rest of my invoices

I have started working through all my personal invoices with the app, and I am
pretty happy with it. It is working well for me. After all the model comparisons,
packaging fixes, and environment repairs, I am actually using it for the thing
I built it to do: tidying invoice filenames. The PDFs have finally become the
main task again.

This is also how I usually develop. I use what I have built, encounter an issue
or notice something that would make it better, then fix it or add the feature.
Using the app is how I work out what should happen next. A plan gets a change
built; living with the result supplies the next set of ideas.

That does not mean I have finished processing every invoice or measured some
new universal accuracy score. I have started using it on my own collection,
and the experience so far is good. That is a more useful milestone for me than
another perfectly cooperative test fixture.

There is still public-release work to do, including dependency notices,
fresh-machine setup, and the remaining platform checks. Meanwhile, the app is
already doing a job for me. I can leave the development environment closed,
open the app, and rename invoices. And if using it reveals the next annoying
detail, that becomes the next block of work. Apparently, tidying the files and
tidying the software can share a backlog.
