FROM node:24-alpine AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
# `postinstall` copies the pdf.js worker out of pdfjs-dist, so the script has
# to exist before `npm ci` runs it. Copied separately from the rest of the
# source to keep the dependency layer cacheable.
COPY scripts ./scripts
RUN npm ci

FROM node:24-alpine AS build

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL
# NEXT_PUBLIC values are inlined into the client bundle at build time, so they
# must arrive as build arguments. Both are publishable browser credentials.
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
# Uploads address the API directly; the proxy caps request bodies at 10MB.
ARG NEXT_PUBLIC_API_ORIGIN
ARG NEXT_PUBLIC_INTERVIEW_LIVEKIT_ENABLED=false
ENV NEXT_PUBLIC_INTERVIEW_LIVEKIT_ENABLED=$NEXT_PUBLIC_INTERVIEW_LIVEKIT_ENABLED
ARG NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED=false
ENV NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED=$NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY \
    NEXT_PUBLIC_API_ORIGIN=$NEXT_PUBLIC_API_ORIGIN

COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
# Regenerate the pdf.js worker here, and fail the build if it is missing.
#
# It cannot arrive any other way. `frontend/public/pdf.worker.min.mjs` is
# gitignored — deliberately, so it cannot drift from the installed pdfjs-dist
# — and `railway up` filters the upload through .gitignore, so it is absent
# from the build context on the platform even though it sits on a laptop after
# `npm install`. The dependencies stage does run `postinstall` and does write
# it, but this stage copies only `node_modules` from there, and the `COPY . .`
# above then overwrites `public/` with the context's copy, which lacks it.
#
# The result was an image whose viewer 404s its own worker, which renders no
# PDF at all while every other page works. The postinstall hook only warns —
# right for an install on a laptop, wrong for the deployment image — so this
# asserts the file exists rather than trusting the exit code.
RUN node scripts/copy-pdf-worker.mjs && test -f public/pdf.worker.min.mjs
RUN npm run build

FROM node:24-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

# next.config.mjs reads BACKEND_URL when the standalone server starts, so it
# must exist at runtime as well as at build time. A platform that injects it
# as a service variable overrides this default.
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
