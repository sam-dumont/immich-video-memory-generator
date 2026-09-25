import ThemedImage from '@theme/ThemedImage';
import useBaseUrl from '@docusaurus/useBaseUrl';
import {useColorMode} from '@docusaurus/theme-common';

interface Props {
  name: string;
  alt: string;
}

export default function ThemedScreenshot({name, alt}: Props) {
  const {colorMode} = useColorMode();
  const sources = {
    light: useBaseUrl(`/img/screenshots/${name}.png`),
    dark: useBaseUrl(`/img/screenshots/dark-${name}.png`),
  };
  return (
    <a href={sources[colorMode]} aria-label={`Open full-size screenshot: ${alt}`}>
      <ThemedImage alt={alt} sources={sources} loading="lazy" style={{cursor: 'zoom-in'}} />
    </a>
  );
}
