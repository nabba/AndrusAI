import type { Root, Blockquote, Paragraph, Text } from 'mdast';
import type { Properties } from 'hast';
import { visit } from 'unist-util-visit';

/**
 * remark plugin: Obsidian-style callouts.
 *
 *   > [!note] Optional Title
 *   > body text
 *
 * Wraps the blockquote in a `div.callout` with `data-callout-type="note"`
 * so CSS can theme it. The title line is extracted and re-rendered.
 */
export function remarkCallouts() {
  const TITLE_RE = /^\[!(\w+)\](.*?)$/;

  return (tree: Root) => {
    visit(tree, 'blockquote', (node: Blockquote) => {
      const first = node.children[0];
      if (!first || first.type !== 'paragraph') return;
      const firstPara = first as Paragraph;
      const firstText = firstPara.children[0];
      if (!firstText || firstText.type !== 'text') return;
      const text = firstText as Text;
      const match = text.value.trimStart().match(TITLE_RE);
      if (!match) return;
      const kind = match[1].toLowerCase();
      const titleTail = match[2].trim();

      // Drop the callout-title token from the first text node.
      const remainder = text.value.replace(TITLE_RE, '').trimStart();
      if (remainder) {
        text.value = remainder;
      } else {
        // Remove the now-empty text node.
        firstPara.children.shift();
        if (firstPara.children.length === 0) {
          node.children.shift();
        }
      }

      // Attach data to blockquote so rehype picks up the attrs.
      node.data = node.data || {};
      const hProps: Properties = {
        ...(node.data.hProperties ?? {}),
        className: ['callout', `callout-${kind}`],
        'data-callout-type': kind,
      };
      if (titleTail) hProps['data-callout-title'] = titleTail;
      node.data.hProperties = hProps;
      node.data.hName = 'div';

      // Inject a title paragraph if provided.
      if (titleTail) {
        const titleNode: Paragraph = {
          type: 'paragraph',
          children: [{ type: 'text', value: titleTail }],
          data: {
            hProperties: { className: ['callout-title'] },
          },
        };
        node.children.unshift(titleNode);
      }
    });
  };
}
