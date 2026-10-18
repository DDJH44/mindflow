# 前端镜像：**多阶段构建**。
#
# ## 为什么不是"用 vite 直接跑"
#
# `vite` / `vite preview` 是开发工具：单线程、不做压缩优化、
# 生产用它会在几十个并发时明显变慢。而 `npm run build` 产出的是
# **静态文件** —— 它们只需要一个静态服务器，nginx 就是最快的那个。
#
# ## 为什么第一阶段的结果要 COPY 到第二阶段
#
# 最终镜像里**不含 node_modules、不含源码、不含构建工具**。
# 只需要 nginx + 约 1 MB 的静态文件。
# 从数百 MB 降到约 50 MB，攻击面也小得多。

# ============ 构建阶段 ============
FROM node:22-alpine AS build

WORKDIR /app

# 依赖层：先复制锁文件。
#
# ⚠️ 项目里是 `pnpm-lock.yaml`，因此用 pnpm 而不是 npm ——
# 混用会导致实际安装的版本与锁文件不一致。
RUN corepack enable

COPY package.json pnpm-lock.yaml* ./

# `--frozen-lockfile`：锁文件与 package.json 不一致时**直接失败**，
# 而不是悄悄解析出一个新版本。
# 这正是"构建可复现"的含义 —— 宁可构建失败，不要产出不确定的镜像。
RUN pnpm install --frozen-lockfile

COPY . .

# 类型检查 + 打包。`npm run build` 里有 `vue-tsc`，
# 类型错误会让构建失败 —— 这正是我们要的：
# 类型错误不该在运行时才被发现。
RUN pnpm run build

# ============ 运行阶段 ============
FROM nginx:1.27-alpine

# 用我们自己的配置替换默认站点。
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/mindflow.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
