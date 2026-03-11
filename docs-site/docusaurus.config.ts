import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Immich Memories',
  tagline: 'Create beautiful video compilations from your Immich photo library',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://sam-dumont.github.io',
  baseUrl: '/immich-video-memory-generator/',

  organizationName: 'sam-dumont',
  projectName: 'immich-video-memory-generator',

  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/sam-dumont/immich-video-memory-generator/tree/main/docs-site/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'Immich Memories',
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/sam-dumont/immich-video-memory-generator',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Quick Start',
              to: '/docs/quick-start',
            },
            {
              label: 'Installation',
              to: '/docs/installation/uv',
            },
            {
              label: 'CLI Reference',
              to: '/docs/cli/overview',
            },
          ],
        },
        {
          title: 'Community',
          items: [
            {
              label: 'GitHub Issues',
              href: 'https://github.com/sam-dumont/immich-video-memory-generator/issues',
            },
            {
              label: 'Immich',
              href: 'https://immich.app/',
            },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/sam-dumont/immich-video-memory-generator',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Immich Memories. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'toml'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
