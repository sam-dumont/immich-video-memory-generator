import ThemedImage from '@theme/ThemedImage';
import useBaseUrl from '@docusaurus/useBaseUrl';

interface Props {
  name: string;
  alt: string;
}

export default function ThemedScreenshot({name, alt}: Props) {
  return (
    <ThemedImage
      alt={alt}
      sources={{
        light: useBaseUrl(`/img/screenshots/${name}.png`),
        dark: useBaseUrl(`/img/screenshots/dark-${name}.png`),
      }}
    />
  );
}
