import ReactMarkdown from "react-markdown";

// Content is reviewed, but render defensively anyway: no raw HTML (react-markdown's
// default), no images, and only a small set of elements.
const ALLOWED = ["p", "code", "pre", "strong", "em", "ul", "ol", "li", "h2", "h3", "h4", "a", "blockquote"];
const ALLOWED_WITHOUT_LINKS = ALLOWED.filter((tag) => tag !== "a");

/** `links={false}` for unreviewed text (AI output): a link becomes plain text. */
export function Markdown({ children, links = true }: { children: string; links?: boolean }) {
  return (
    <div className="prose-ks">
      <ReactMarkdown
        allowedElements={links ? ALLOWED : ALLOWED_WITHOUT_LINKS}
        unwrapDisallowed
        components={{
          a: ({ href, children: text }) => (
            <a href={href} rel="noopener noreferrer nofollow" target="_blank">
              {text}
            </a>
          ),
          h2: ({ children: text }) => <h3 className="mt-4 text-lg font-semibold">{text}</h3>,
          h3: ({ children: text }) => <h4 className="mt-3 font-semibold">{text}</h4>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
