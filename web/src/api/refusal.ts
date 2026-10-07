// V2-P5-041. Turning this service's refusal bodies back into the sentences they carry.
//
// ## The defect
//
// `requestJson` threw `new Error(await response.text())` and every page renders
// `error.message` verbatim, so all four pages showed a user the raw JSON of a refusal:
//
//     {"detail":{"reason":"panel_unreadable","message":"the XSHG calendar could not be
//     read out of this service's panel store: … Build it first (`openalpha panel build
//     --dataset trade_cal --year <year>`) …"}}
//
// The server's message is the good part — it names the command that fixes the problem —
// and it was the part buried in punctuation. Unwrapping happens **here**, in the one
// module that knows the wire, rather than in each of the four pages: a page that had to
// learn the shape of `detail` would be a fifth place for the shape to be learned wrong.
//
// ## The shapes, and why the reader may not switch on the status code
//
// Four bodies reach a browser, and `docs/api/http.md` documents all four. Each row below
// was captured with `curl` against `openalpha serve` on a seeded temp runtime dir rather
// than transcribed from the prose:
//
// | Body | Example route | Rendered as |
// |---|---|---|
// | `{"detail": {"reason", "message", …}}` | `/api/v1/panel/health`, `/api/v1/shortlists/{id}` | `detail.message` |
// | `{"detail": [ {loc, msg, type, input}, … ]}` | any pydantic validation failure | one line per field |
// | `{"detail": "…"}` | the two portfolio order routes | the string |
// | not JSON at all | a proxy or an unmapped `500` | itself |
//
// **The status code is not the discriminator and must not become one.** The panel plane's
// `panel_unreadable` is a **409**, not a 422 — measured, not assumed — and `409` also
// carries the flat gate verdict, which has no `detail` key at all. So this reads the body
// and treats the status only as the last resort when there is no body to read.
//
// ## Why the pydantic list is not flattened
//
// A field-error list is not one refusal, it is N of them, and `loc` is the only thing that
// says which field each belongs to. `V2-P4-051` pinned that shape with tests precisely so
// a caller could tell `dataset` from `year`; joining the `msg`s and dropping the `loc`s
// would hand back "Field required, Field required, Field required". Each entry therefore
// renders as `<path>：<msg>`, with `loc` rendered as an addressable path
// (`body.evidence[1].payload.quality_flags[1]`) so the position inside a collection
// survives too.
//
// The one entry with nothing to address is the truncation sentinel the same contract
// appends — `{"loc": [], "type": "errors_elided", "msg": "N further validation error(s)
// were not listed"}` — which renders as its bare sentence rather than as a separator with
// an empty path in front of it.

/** The separator between a field path and its message. Full-width, because the surface is
 * Chinese and every other label on these pages uses it. */
const SEPARATOR = "：";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * One pydantic `loc` as a path a reader can follow back to the field.
 *
 * Names join with dots and indexes take brackets, so `["body", "evidence", 1, "payload"]`
 * reads `body.evidence[1].payload` — the same way the field would be addressed in the
 * request that was refused. An empty `loc` addresses nothing and renders empty, which is
 * what the `errors_elided` sentinel needs.
 */
export function formatLocation(loc: readonly (string | number)[]): string {
  let path = "";
  for (const segment of loc) {
    if (typeof segment === "number") {
      path += `[${segment}]`;
    } else if (path === "") {
      path = segment;
    } else {
      path += `.${segment}`;
    }
  }
  return path;
}

/** One entry of a pydantic field-error list, as `<path>：<msg>`. */
function entryLine(entry: unknown): string {
  if (!isRecord(entry)) return String(entry);
  const message = typeof entry.msg === "string" ? entry.msg : JSON.stringify(entry);
  const where = Array.isArray(entry.loc)
    ? formatLocation(entry.loc as (string | number)[])
    : "";
  return where === "" ? message : `${where}${SEPARATOR}${message}`;
}

/**
 * The sentence a refused response is trying to say.
 *
 * Never throws and never returns an empty string: a shape this reader does not recognise
 * comes back as the raw body, because an unrecognised refusal is still information and
 * replacing it with a generic sentence would be this module's own defect in the other
 * direction — the user would lose the server's words a second time.
 */
export function refusalMessage(status: number, body: string): string {
  const raw = body.trim();
  if (raw === "") return `请求失败：HTTP ${status}`;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
  if (!isRecord(parsed)) return raw;

  const detail = parsed.detail;

  if (typeof detail === "string" && detail.trim() !== "") {
    return detail.trim();
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map(entryLine).join("\n");
  }
  if (isRecord(detail) && typeof detail.message === "string" && detail.message.trim() !== "") {
    return detail.message.trim();
  }
  return raw;
}
