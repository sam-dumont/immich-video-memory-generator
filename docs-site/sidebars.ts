import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

// Pages under docs/being-rewritten/ are the old text the new pages are written
// from. They are unlisted, on purpose absent from this tree, and each one goes
// away with the PR that finishes its last destination (epic #1276).
const sidebars: SidebarsConfig = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Welcome',
      collapsed: false,
      items: [
        {type: 'doc', id: 'welcome/introduction', label: 'Introduction'},
        {type: 'doc', id: 'welcome/about', label: 'Why this exists'},
      ],
    },
    {
      type: 'category',
      label: 'Get started',
      collapsed: false,
      items: [
        'get-started/quick-start',
        {type: 'doc', id: 'get-started/first-film', label: 'Your first film'},
        'get-started/who-is-who',
      ],
    },
    {
      type: 'category',
      label: 'Make films',
      items: [
        {type: 'doc', id: 'make/memory-types', label: 'Memory types'},
        {type: 'doc', id: 'make/web-ui', label: 'The web UI'},
        {type: 'doc', id: 'make/titles-maps-music', label: 'Titles, maps and music'},
        {type: 'doc', id: 'make/photos-and-live-photos', label: 'Photos, Live Photos and HDR'},
        {type: 'doc', id: 'make/automate', label: 'Automate it'},
        {
          type: 'category',
          label: 'The CLI',
          items: [
            {type: 'doc', id: 'make/cli/generate', label: 'generate'},
            {type: 'doc', id: 'make/cli/prepare', label: 'prepare'},
            {type: 'doc', id: 'make/cli/runs', label: 'runs'},
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'How it chooses',
      items: [
        'how-it-chooses/overview',
        'how-it-chooses/moments-and-stories',
        'how-it-chooses/picking-shots',
        'how-it-chooses/family-audience-duplicates',
        'how-it-chooses/length-and-filler',
        'how-it-chooses/what-a-model-adds',
        'how-it-chooses/overrule-it',
        'how-it-chooses/glossary',
      ],
    },
    {
      type: 'category',
      label: 'Run it',
      items: [
        'run/requirements',
        {type: 'doc', id: 'run/docker', label: 'Docker Compose [Recommended]'},
        {type: 'doc', id: 'run/nas', label: 'On a NAS (Synology, Unraid)'},
        {type: 'doc', id: 'run/uv-pip', label: 'pip / uv [Advanced]'},
        {type: 'doc', id: 'run/kubernetes', label: 'Kubernetes [Advanced]'},
        {type: 'doc', id: 'run/terraform', label: 'Terraform [Advanced]'},
        {type: 'doc', id: 'run/config-file', label: 'Configuration file'},
        {type: 'doc', id: 'run/environment-variables', label: 'Environment variables'},
        {type: 'doc', id: 'run/authentication', label: 'Authentication'},
        {type: 'doc', id: 'run/privacy', label: 'Privacy: what leaves your network'},
        {type: 'doc', id: 'run/hardware', label: 'Hardware encoding'},
        {
          type: 'category',
          label: 'Upgrading, health, logs, cache',
          items: [
            {type: 'doc', id: 'run/maintenance/upgrading', label: 'Upgrading'},
            {type: 'doc', id: 'run/maintenance/health-logs-cache', label: 'Health, logs and caches'},
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'Make it better (optional)',
      items: [
        'better/overview',
        {type: 'doc', id: 'better/reader', label: 'Add a reader (local or hosted)'},
        {type: 'doc', id: 'better/captions', label: 'Add captions'},
        {type: 'doc', id: 'better/inference', label: 'Inference on a GPU box'},
        'better/gpu-render',
        'better/music',
        'better/measured',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        {type: 'doc', id: 'reference/cli-reference', label: 'CLI reference'},
        {type: 'doc', id: 'reference/config-reference', label: 'Config reference'},
        'reference/troubleshooting',
        'reference/faq',
      ],
    },
    {
      type: 'category',
      label: 'Contribute',
      items: [
        'contribute/testing',
        'contribute/development-setup',
        'contribute/demo-assets',
        'contribute/setup-matrix',
        'contribute/architecture',
        'contribute/code-of-conduct',
      ],
    },
  ],
};

export default sidebars;
