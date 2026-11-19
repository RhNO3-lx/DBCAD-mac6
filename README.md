# DBCAD-mac6

在两台设备间传输 DBCAD 交接包。当前包适用于 Apple Silicon Mac，平台源码基线为 b715e61。

1. 登录此 GitHub 账号，打开 [最新交接版本](../../releases/latest)，下载 Assets 下面的 tar.gz 和对应的 SHA-256 文件。
2. 在下载目录校验并解包。

       shasum -a 256 -c DBCAD-mac6-macos-arm64-b715e61.tar.gz.sha256
       tar -xzf DBCAD-mac6-macos-arm64-b715e61.tar.gz

3. 打开解出的 DBCAD-mac6，按 doc/上手指南.md 安装。运行 Install-DBCAD.command 后，再运行 Launch-DBCAD.command。

后续交接可以在这个私有仓库创建新 Release，把新版压缩包和对应校验文件放到 Assets。
