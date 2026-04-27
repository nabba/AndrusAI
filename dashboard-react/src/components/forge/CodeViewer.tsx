import { useEffect, useRef } from 'react';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/github-dark.css';

hljs.registerLanguage('python', python);
hljs.registerLanguage('json', json);

interface Props {
  code: string;
  language: 'python' | 'json';
}

export function CodeViewer({ code, language }: Props) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (ref.current) {
      ref.current.removeAttribute('data-highlighted');
      hljs.highlightElement(ref.current);
    }
  }, [code, language]);

  return (
    <pre className="rounded-lg bg-[#0a0e14] border border-[#1e2738] overflow-x-auto text-xs">
      <code ref={ref} className={`language-${language} block p-4`}>
        {code}
      </code>
    </pre>
  );
}
