# Bundled fonts

`BarlowCondensed-500.ttf` and `BarlowCondensed-600.ttf` are the Latin subsets of
Barlow Condensed, served by Google Fonts and licensed under the SIL Open Font
License 1.1. <https://fonts.google.com/specimen/Barlow+Condensed>

They are bundled because `cv2.FontFace` renders the annotation drawn onto the survey
frames, and the annotation is meant to read like the single-stroke lettering of a
drawing rather than like a website. The web interface loads Barlow, Barlow Condensed
and Spectral from Google Fonts at run time and bundles nothing.

OFL 1.1 permits bundling and redistribution. It is not a copyleft licence in the
AGPL sense and places no obligation on this project's own source.
