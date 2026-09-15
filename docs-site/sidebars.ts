import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    {
      type: 'category',
      label: 'Welcome',
      collapsed: false,
      items: [
        'welcome/overview',
        'welcome/why-immich-memories',
        'welcome/built-with-ai',
      ],
    },
    {
      type: 'category',
      label: 'Create memories',
      collapsed: false,
      items: [
        'create/first-memory',
        'create/web-ui',
        {
          type: 'category',
          label: 'The CLI',
          items: [
            'create/cli/generate',
            'create/cli/prepare',
            'create/cli/auto',
            'create/cli/runs',
            'create/cli/scheduler',
          ],
        },
        'create/memory-types',
        'create/recipes/automated-generation',
        'create/pipeline',
        'create/photos-and-live-photos',
        'create/titles-and-music',
      ],
    },
    {
      type: 'category',
      label: 'Deploy and operate',
      items: [
        'deploy/self-hosting',
        'deploy/running-modes',
        'deploy/readers',
        {
          type: 'category',
          label: 'Installation',
          collapsed: false,
          items: [
            'deploy/installation/docker',
            'deploy/installation/uv-pip',
            'deploy/installation/inference-service',
            'deploy/installation/caption-server',
            'deploy/installation/kubernetes',
            'deploy/installation/terraform',
          ],
        },
        {
          type: 'category',
          label: 'Configuration',
          items: [
            'deploy/configuration/config-file',
            'deploy/configuration/environment-variables',
            'deploy/configuration/authentication',
            'deploy/configuration/network-and-privacy',
            'deploy/configuration/editorial-preparation',
          ],
        },
        'deploy/hardware',
        {
          type: 'category',
          label: 'Common setups',
          items: [
            'deploy/common-setups/nas-only',
            'deploy/common-setups/mac-local-llm',
            'deploy/common-setups/linux-nvidia',
            'deploy/common-setups/kubernetes-gpu',
          ],
        },
        {
          type: 'category',
          label: 'Monitoring and maintenance',
          items: [
            'deploy/maintenance/health-logs-cache',
            'deploy/maintenance/upgrading',
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/cli-reference',
        'reference/config-reference',
        'reference/architecture',
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
        'contribute/code-of-conduct',
      ],
    },
  ],
};

export default sidebars;
