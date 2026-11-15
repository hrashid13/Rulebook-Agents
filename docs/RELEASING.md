# Releasing

1. Merge everything for the release into `main`, with CI green.
2. Set the version in `src/rulebook/__init__.py` and add a section to `CHANGELOG.md`.
3. On GitHub: **Releases > Draft a new release**. Create the tag `vX.Y.Z` on `main`, title it `vX.Y.Z`, paste the changelog section, tick **Publish this Action to the GitHub Marketplace** (first release: accept the agreement, pick the categories *Code quality* and *Security*), and publish.
4. Move the major tag so `@v1` users get the release:

   ```
   git fetch --tags
   git tag -f v1 vX.Y.Z
   git push -f origin v1
   ```

A breaking change (an input removed or renamed, a different workflow needed) gets a new major version and a new major tag, so existing `@v1` users are not broken.
