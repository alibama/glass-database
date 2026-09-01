# Accessibility — mDLAUG alignment (objects view)

The **Explore → Objects** view is built to the [Mobile Digital Library
Accessibility and Usability Guidelines (mDLAUG)](https://sites.uwm.edu/mdlaug/),
which target blind and low-vision users navigating digital libraries with screen
readers on mobile. It's meant as a working sandbox: run the
[mDLAUG repair extension](https://github.com/alibama/mdlaug) against it and it
should find little to fix.

Rather than let Streamlit emit generic markup, the objects collection is rendered
as purpose-built accessible HTML (`explore/objects_a11y.py`, `st.html`). Each
element is tagged with `data-mdlaug-ok="<code>"` so the situation it addresses is
inspectable in DevTools.

## What the objects view does

| mDLAUG | Situation | In the objects view |
|---|---|---|
| COM1 | Understand DL structure | Skip link to the list; labelled region + headings |
| RED1 | Recognize results availability | `role="status"` live region — "N objects found" / "none" |
| ACC4 | Access collection items | Items are a named `role="list"` (`aria-label`) |
| EVA1 | Assess relevance | Each item names title + maker + year |
| NAV3 | Navigate results | Screen-reader "Item N of M" on every object |
| NAV4 | Navigate within a long item | "Contents" jump-links to each object |
| ACC2/COM3 | Images | Real descriptive `alt` from the object's own metadata (never a filename or empty) |
| ACC3/COM4 | Structured data | Provenance events as a real `<table>` with `<th scope="col">`; facts as a `<dl>` |
| ACC1 | Access files | Download links name **format + size**; external verify link warns it opens a new tab |
| RED4 | Recognize authorized/limited features | Provenance is labelled "unverified"; a self-signed credential says why it's untrusted |

All contributed text is HTML-escaped before rendering (safe in `st.html`, and
verified by a test).

Regression tests in `tests/test_objects_a11y.py` assert these features stay put
(live region, named list, per-item position, descriptive alt, data-table events,
accessible file links, escaping, jump-links).

## Honest limitations

- This covers the object **content**. The surrounding Streamlit chrome — the
  sidebar controls, the top nav, headings emitted by `st.title` — is only partly
  under our control; mDLAUG conformance there is best-effort.
- The **Datasets** view's charts are still Streamlit/Altair widgets, which aren't
  screen-reader-friendly (mDLAUG ACC3/COM4). The data table beneath each dataset
  is the accessible alternative for now; giving the charts a `role="img"` +
  long-description is the natural next step.
- Alt text is generated from each object's metadata. For a description of what the
  photograph actually depicts, wire a describe-image model to enrich it — the same
  `onNeedAltText` idea the repair extension uses.

## Testing it

- Open `objects-a11y-sample.html` (a standalone snapshot) in a browser and run the
  mDLAUG extension's **Assess this page** / **Highlight changes**.
- Or point the extension at the live `Explore → Objects` page.
- `make test` runs the accessibility regression tests.
