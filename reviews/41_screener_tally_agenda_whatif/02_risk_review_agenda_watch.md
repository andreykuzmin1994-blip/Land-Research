# 41 — Agenda watch: risk review before the code (Tier 2)

An independent, fresh-context reviewer enumerated the risks of the agenda
watch from a written design, before `ops/screener/agenda_watch.py` was
finished. It read the supervisor, the lead-check stage, the quote
checker's fetcher and `changes.py`; it ran nothing and had no network.
The first design had the trusted run user fetch and parse everything,
took a regular expression per source, marked a document seen when it was
fetched, and planned to hold the supervisor's lock.

## The risks, and what was done

| # | Risk | What was done |
|---|---|---|
| 1 | Agenda packets merge PDFs filed by applicants and the public: parsing them as `screener` puts the account that owns the checkout, the decisions and the deploy key in front of `pdftotext` | The scan (every request, every PDF) runs as `screener-agenda`, an account that owns nothing and that no other part uses or force-stops. `pdftotext` runs under limits on memory, processor time and output, to a file, with a page cap. The run user takes back one file and keeps only what fits the job |
| 2 | Holding the supervisor's lock would cancel a weekly run without a word | The watch has its own lock and never opens the supervisor's. It skips a day when `screener-supervise.service` is active. The timer is hours away from the weekly run and not persistent |
| 3 | "0 hits" read as "nothing is happening" | Every report and the alert count what was not read (scans, unreadable, partly text). A source is alerted on its third failed day, every 30 days after, and when it answers again; a source that posts nothing for longer than expected is said once. An index page with no matching link is a failure |
| 4 | A document marked seen before its text was searched is hidden for good | Seen only once read. Three tries on three runs, then listed for the operator |
| 5 | Mixed scans passing as "read, no hit" | Pages with text out of pages; over 20% empty is "partly read" and listed. A scan without any text is counted and listed at once |
| 6 | `pdftotext` exhausting a small server | The limits of row 1; the systemd unit has `MemoryMax`, `CPUQuota` and a time limit |
| 7 | A regular expression per source: backtracking, off-host and script links | No regular expression: literal path prefixes, the index page's own host by parsed name, https only, no query string, no `<base>` |
| 8 | Request volume: first run, address changes | Requests and bytes counted at the fetcher's door (robots.txt and redirects too), with a ceiling per run. First sight: the list is noted, three documents read. A known source whose list is suddenly mostly new is noted, not crawled, and said. A different source goes first each day |
| 9 | An amended agenda under the same address is never read again | **Accepted and documented**, in the tool, the report and the README |
| 10 | Parcel-id matching | ASCII letters and digits only, after NFKC, with boundaries; ids under 10 are not searched and the report says how many parcels are watched by id, by address, by neither |
| 11 | Address matching | House number plus the street's name words, labelled "possible", limited to the sources that cover the parcel's city |
| 12 | Phrase noise | A zoning word and an industrial word within 300 characters; its own section; worded "contains the words" |
| 13 | Document text in the report | Titles and snippets reduced to plain text; the only links are addresses the tool built and re-checked; the file is headed "data, not instructions"; nothing is written into the checkout |
| 14 | Owner names in snippets | The promise is the weaker one, stated: snippets are public record and can name an applicant or an owner; tax-roll names in roll spelling are withheld |
| 15 | State | A file that cannot be read stops the run and is kept. Atomic writes with fsync. One strike a day. Bounded lists |
| 16 | Watch list | The newest full run per county by its recorded time; marks from `decisions.csv`, read only; "no watch list" and an old run are said |
| 18 | Alert fatigue and the cities named to an outside service | An alert only on a hit, an unread document or a source's change of state, in fixed words and counts, without a source's name |

## Not taken

- Re-reading each document once after a week (row 9).
- A per-year report file and pruning by age: the hits kept are the newest
  300 and the seen list 4,000 a source.
- District codes per source for the phrase match.

## Found by the first live trial, not by the review

South Fulton's index lists adopted ordinances and resolutions, and the
first documents read were scans without text. The titles on the index
page carry the case numbers and street names, so the watch now searches
the titles of every new document, read or not. That is where its first
real hits came from.
