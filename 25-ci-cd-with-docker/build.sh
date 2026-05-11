#!/bin/bash
set -e

IMAGE_NAME="<myusername>/backend"
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(date -u +%Y%m%d-%H%M%S)
VERSION=${1:-"dev"}   # pass version as first argument, default to "dev"

echo "Building ${IMAGE_NAME}:${VERSION}"
echo "Git SHA: ${GIT_SHA}"
echo "Build time: ${BUILD_TIME}"

# Build once
docker build -t ${IMAGE_NAME}:${VERSION} ./backend

# Apply all tags
docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:git-${GIT_SHA}
docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:${BUILD_TIME}-${GIT_SHA}

# If this is a real version (not dev), also tag latest
if [ "${VERSION}" != "dev" ]; then
  docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:latest
fi

echo "Tags applied:"
docker image ls ${IMAGE_NAME}

# Push all tags
docker push ${IMAGE_NAME}:${VERSION}
docker push ${IMAGE_NAME}:git-${GIT_SHA}
docker push ${IMAGE_NAME}:${BUILD_TIME}-${GIT_SHA}

if [ "${VERSION}" != "dev" ]; then
  docker push ${IMAGE_NAME}:latest
fi

echo "Done. Image: ${IMAGE_NAME}:${VERSION}"
echo "Image tag: ${IMAGE_NAME}:${VERSION}"
docker manifest inspect ${IMAGE_NAME}:${VERSION} | grep digest | head -1