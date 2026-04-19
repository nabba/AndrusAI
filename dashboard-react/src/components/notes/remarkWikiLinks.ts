import type { Root, Text, Link } from 'mdast';
import { visit, SKIP } from 'unist-util-visit';

/**
 * remark plugin: convert [[Target]] and [[Target|Alias]] into link nodes
 * with a custom `data.hProperties.dataWikilink` attribute so the MDX
 * renderer can resolve the href at render time.
 */
export function remarkWikiLinks() {
  const RE = /\[\[([^\]\r\n|#^]+?)(?:[#^][^\]|]*)?(?:\|([^\]]+))?\]\]/g;

  return (tree: Root) => {
    visit(tree, 'text', (node: Text, index, parent) => {
      if (!parent || index == null) return;
      if (parent.type === 'link') return;

      RE.lastIndex = 0;
      const value = node.value;
      if (!RE.test(value)) return;

      RE.lastIndex = 0;
      const parts: Array<Text | Link> = [];
      let last = 0;
      let match: RegExpExecArray | null;
      while ((match = RE.exec(value)) !== null) {
        const [full, target, alias] = match;
        if (match.index > last) {
          parts.push({ type: 'text', value: value.slice(last, match.index) });
        }
        const label = alias?.trim() || target.trim();
        const href = `#wiki:${encodeURIComponent(target.trim())}`;
        parts.push({
          type: 'link',
          url: href,
          children: [{ type: 'text', value: label }],
          data: {
            hProperties: {
              'data-wikilink': target.trim(),
              className: ['wikilink'],
            },
          },
        } as Link);
        last = match.index + full.length;
      }
      if (last < value.length) {
        parts.push({ type: 'text', value: value.slice(last) });
      }

      // Replace node in place.
      (parent.children as Array<Text | Link>).splice(index, 1, ...parts);
      return [SKIP, index + parts.length];
    });
  };
}
