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
        'welcome/quick-start',
        'welcome/built-with-ai',
      ],
    },
    {
      type: 'category',
      label: 'Create Memories',
      collapsed: false,
      items: [
        'create/first-memory',
        {
          type: 'category',
          label: 'Using the Web UI',
          items: [
            'create/web-ui/memory',
            'create/web-ui/settings',
          ],
        },
        {
          type: 'category',
          label: 'Using the CLI',
          items: [
            'create/cli/generate',
            'create/cli/prepare',
            'create/cli/auto',
            'create/cli/music',
            'create/cli/titles',
            'create/cli/scheduler',
            'create/cli/runs',
            'create/cli/discover-days',
            'create/cli/people',
            'create/cli/discovery-and-utility',
          ],
        },
        {
          type: 'category',
          label: 'Memory Types',
          items: [
            'create/memory-types/year-in-review',
            'create/memory-types/monthly-person-season',
            'create/memory-types/holiday',
            'create/memory-types/trip-memories',
            'create/memory-types/album-memories',
            'create/memory-types/special-days',
          ],
        },
        {
          type: 'category',
          label: 'Recipes',
          items: [
            'create/recipes/birthday-compilations',
            'create/recipes/automated-generation',
            'create/recipes/trigger-endpoint',
            'create/recipes/tips-and-best-practices',
            'create/recipes/matrix-routes',
          ],
        },
        {
          type: 'category',
          label: 'Understanding the Pipeline',
          items: [
            'create/pipeline/pipeline-overview',
            'create/pipeline/the-curator',
            'create/pipeline/rules-mode',
            'create/pipeline/face-aware-cropping',
            'create/pipeline/duplicate-detection',
            'create/pipeline/live-photos',
            'create/pipeline/photo-support',
            'create/pipeline/hdr',
            'create/pipeline/llm-content-analysis',
            'create/pipeline/title-screens-and-maps',
            'create/pipeline/audio-and-music',
            'create/pipeline/privacy-mode',
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'Deploy & Operate',
      items: [
        'deploy/self-hosting',
        'deploy/running-modes',
        {
          type: 'category',
          label: 'Installation',
          collapsed: false,
          items: [
            'deploy/installation/docker',
            'deploy/installation/uv-pip',
            'deploy/installation/inference-service',
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
        {
          type: 'category',
          label: 'Hardware Acceleration',
          items: [
            'deploy/hardware/overview',
            'deploy/hardware/nvidia',
            'deploy/hardware/apple-silicon',
            'deploy/hardware/intel-qsv',
            'deploy/hardware/amd-vaapi',
            'deploy/hardware/cpu-only',
          ],
        },
        {
          type: 'category',
          label: 'Common Setups',
          items: [
            'deploy/common-setups/nas-only',
            'deploy/common-setups/mac-local-llm',
            'deploy/common-setups/linux-nvidia',
            'deploy/common-setups/kubernetes-gpu',
          ],
        },
        {
          type: 'category',
          label: 'Monitoring & Maintenance',
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
