import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github-dark.css';

import { remarkWikiLinks } from './remarkWikiLinks';
import { remarkCallouts } from './remarkCallouts';
import { MermaidBlock } from './MermaidBlock';
import { endpoints } from '../../api/endpoints';

interface Props {
  body: string;
  root: string;
  currentPath: string;
  onNavigate: (wikilinkTarget: string, isMarkdownLink: boolean) => void;
}

export function NoteRenderer({ body, root, currentPath, onNavigate }: Props) {
  const components = useMemo<Components>(() => {
    // Resolve a markdown image/file reference relative to the current note.
    const resolveAttachment = (url: string): string => {
      // External or anchor — return as-is.
      if (/^(https?:|mailto:|#)/.test(url)) return url;
      const basedir = currentPath.includes('/')
        ? currentPath.slice(0, currentPath.lastIndexOf('/'))
        : '';
      // Normalise `./` prefix.
      const cleaned = url.replace(/^\.\//, '');
      const joined = basedir ? `${basedir}/${cleaned}` : cleaned;
      // Collapse any `..` segments so the backend path-safety check doesn't reject us.
      const parts: string[] = [];
      for (const seg of joined.split('/')) {
        if (seg === '..') parts.pop();
        else if (seg && seg !== '.') parts.push(seg);
      }
      const rel = parts.join('/');
      return endpoints.notesAttachment(root, rel);
    };

    return {
      a({ node, href, className, children, ...rest }) {
        const wikiTarget = node?.properties?.['data-wikilink'] as string | undefined;
        if (wikiTarget) {
          return (
            <a
              href={href ?? `#wiki:${wikiTarget}`}
              className="text-[#a78bfa] underline decoration-dotted hover:text-[#c4b5fd] hover:decoration-solid"
              onClick={(e) => {
                e.preventDefault();
                onNavigate(wikiTarget, false);
              }}
            >
              {children}
            </a>
          );
        }
        if (href && /^(https?:|mailto:|#)/.test(href)) {
          return (
            <a
              href={href}
              target={href.startsWith('http') ? '_blank' : undefined}
              rel="noopener noreferrer"
              className={`text-[#60a5fa] hover:underline ${className ?? ''}`}
              {...rest}
            >
              {children}
            </a>
          );
        }
        // Internal markdown link — try to resolve as a note path within the same root.
        if (href && (/\.(md|mdx|markdown)$/i.test(href) || !href.includes('.'))) {
          return (
            <a
              href={href}
              className="text-[#60a5fa] hover:underline"
              onClick={(e) => {
                e.preventDefault();
                const basedir = currentPath.includes('/')
                  ? currentPath.slice(0, currentPath.lastIndexOf('/'))
                  : '';
                const cleaned = href.replace(/^\.\//, '').split('#')[0];
                const joined = basedir ? `${basedir}/${cleaned}` : cleaned;
                onNavigate(joined, true);
              }}
            >
              {children}
            </a>
          );
        }
        if (href) {
          return (
            <a href={resolveAttachment(href)} target="_blank" rel="noopener noreferrer" className="text-[#60a5fa] hover:underline">
              {children}
            </a>
          );
        }
        return <a {...rest}>{children}</a>;
      },
      img({ src, alt, ...rest }) {
        const resolved = typeof src === 'string' ? resolveAttachment(src) : src;
        return <img src={resolved as string} alt={alt ?? ''} loading="lazy" className="max-w-full h-auto rounded-md" {...rest} />;
      },
      code({ className, children, ...rest }) {
        const match = /language-(\w+)/.exec(className || '');
        const lang = match?.[1];
        if (lang === 'mermaid') {
          return <MermaidBlock source={String(children).trim()} />;
        }
        return (
          <code className={className} {...rest}>
            {children}
          </code>
        );
      },
      // Tables — style overrides so they read well on the dark bg.
      table({ children }) {
        return (
          <div className="overflow-x-auto my-3">
            <table className="w-full text-sm border-collapse">{children}</table>
          </div>
        );
      },
      th({ children }) {
        return (
          <th className="text-left px-3 py-2 border-b border-[#1e2738] text-xs font-semibold text-[#7a8599] uppercase tracking-wider">
            {children}
          </th>
        );
      },
      td({ children }) {
        return <td className="px-3 py-2 border-b border-[#1e2738]/60 text-[#e2e8f0]">{children}</td>;
      },
    };
  }, [root, currentPath, onNavigate]);

  return (
    <div className="note-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkCallouts, remarkWikiLinks]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
        components={components}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}
