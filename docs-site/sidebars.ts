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
            'create/web-ui/step1-configuration',
            'create/web-ui/step2-clip-review',
            'create/web-ui/step3-generation-options',
            'create/web-ui/step4-preview-export',
          ],
        },
        {
          type: 'category',
          label: 'Using the CLI',
          items: [
            'create/cli/generate',
            'create/cli/music',
            'create/cli/titles',
            'create/cli/scheduler',
            'create/cli/runs',
          ],
        },
        {
          type: 'category',
          label: 'Memory Types',
          items: [
            'create/memory-types/year-in-review',
            'create/memory-types/monthly-person-season',
            'create/memory-types/trip-memories',
          ],
        },
        {
          type: 'category',
          label: 'Recipes',
          items: [
            'create/recipes/birthday-compilations',
            'create/recipes/automated-generation',
            'create/recipes/tips-and-best-practices',
          ],
        },
        {
          type: 'category',
          label: 'Understanding the Pipeline',
          items: [
            'create/pipeline/clip-selection-scoring',
            'create/pipeline/face-aware-cropping',
            'create/pipeline/scene-detection',
            'create/pipeline/duplicate-detection',
            'create/pipeline/live-photos',
            'create/pipeline/photo-support',
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
        {
          type: 'category',
          label: 'Installation',
          collapsed: false,
          items: [
            'deploy/installation/docker',
            'deploy/installation/uv-pip',
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
        'contribute/code-of-conduct',
      ],
    },
  ],
};

export default sidebars;
