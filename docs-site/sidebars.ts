import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    'intro',
    'quick-start',
    {
      type: 'category',
      label: 'Installation',
      collapsed: false,
      items: [
        'installation/uv',
        'installation/pip',
        'installation/docker',
        'installation/kubernetes',
        'installation/terraform',
      ],
    },
    {
      type: 'category',
      label: 'UI Walkthrough',
      items: [
        'ui-walkthrough/overview',
        'ui-walkthrough/step1-configuration',
        'ui-walkthrough/step2-clip-review',
        'ui-walkthrough/step3-generation-options',
        'ui-walkthrough/step4-preview-export',
      ],
    },
    {
      type: 'category',
      label: 'CLI Reference',
      items: [
        'cli/overview',
        'cli/generate',
        'cli/people-years',
        'cli/music',
        'cli/hardware',
        'cli/runs',
        'cli/titles',
        'cli/reference',
      ],
    },
    {
      type: 'category',
      label: 'Configuration',
      items: [
        'configuration/config-file',
        'configuration/env-variables',
        'configuration/time-periods',
      ],
    },
    {
      type: 'category',
      label: 'Features',
      items: [
        'features/smart-clip-selection',
        'features/face-aware-cropping',
        'features/duplicate-detection',
        'features/scene-detection',
        'features/llm-analysis',
        'features/title-screens',
        'features/audio-ducking',
      ],
    },
    {
      type: 'category',
      label: 'Hardware Acceleration',
      items: [
        'hardware/overview',
        'hardware/nvidia',
        'hardware/apple-silicon',
        'hardware/intel-qsv',
        'hardware/amd-vaapi',
        'hardware/cpu-only',
      ],
    },
    {
      type: 'category',
      label: 'AI Music',
      items: [
        'music/overview',
        'music/ace-step',
        'music/musicgen',
        'music/multi-provider',
        'music/llm-setup',
        'music/custom-music',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'guides/first-video',
        'guides/birthday-compilation',
        'guides/automation',
        'guides/best-practices',
      ],
    },
    'troubleshooting',
    'faq',
  ],
};

export default sidebars;
