use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tauri::{AppHandle, Emitter, Manager, Window};

const PACKAGED_RUNTIME_ROOT: &str = "ordo-runtime";
const PACKAGED_REPO_ROOT: &str = "repo";
const PACKAGED_PYTHON_ROOT: &str = "python";
const PACKAGED_PYTHON_EXECUTABLE: &str = "python/bin/python3";
const PACKAGED_NODE_ARCHIVE: &str = "node-runtime.tar.gz";
const PACKAGED_NODE_EXECUTABLE: &str = "node/bin/node";
const PRESERVED_RUNTIME_FILES: &[&str] = &["repo/secrets.env", "repo/config.json"];

#[derive(Debug, Clone, PartialEq, Eq)]
enum RuntimeSource {
    Packaged,
    Explicit,
    Development,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RuntimeContext {
    source: RuntimeSource,
    repo_root: PathBuf,
    python_executable: PathBuf,
    python_home: Option<PathBuf>,
    python_path: Vec<PathBuf>,
    node_executable: Option<PathBuf>,
}

impl RuntimeContext {
    fn bridge_script_path(&self) -> PathBuf {
        self.repo_root.join("scripts").join("workbench_bridge.py")
    }

    fn python_display(&self) -> String {
        self.python_executable.display().to_string()
    }

    fn apply_runtime_env(&self, command: &mut Command) -> Result<(), String> {
        if let Some(home) = &self.python_home {
            command.env("PYTHONHOME", home);
        }
        if !self.python_path.is_empty() {
            let joined = env::join_paths(self.python_path.iter())
                .map_err(|err| format!("拼接包内 PYTHONPATH 失败: {err}"))?;
            command.env("PYTHONPATH", joined);
        }
        if let Some(node_executable) = &self.node_executable {
            command.env("ORDO_NODE", node_executable);
        }
        Ok(())
    }
}

fn validate_repo_root(path: PathBuf) -> Result<PathBuf, String> {
    let bridge_script = path.join("scripts").join("workbench_bridge.py");
    if bridge_script.is_file() {
        Ok(path)
    } else {
        Err(format!(
            "无法定位发布引擎目录：缺少 {}",
            bridge_script.display()
        ))
    }
}

fn format_repo_root_error(path: &Path, err: String, explicit: bool) -> String {
    if explicit {
        format!(
            "{err}。当前 ORDO_REPO_ROOT={}; 请把它指向包含 scripts/workbench_bridge.py 的仓库根目录。",
            path.display()
        )
    } else {
        format!(
            "{err}。如果桌面壳不是从源码仓库目录启动，请设置 ORDO_REPO_ROOT=/path/to/tiandidistribute。"
        )
    }
}

fn resolve_repo_root_from(
    manifest_dir: PathBuf,
    explicit_root: Option<PathBuf>,
) -> Result<PathBuf, String> {
    if let Some(root) = explicit_root {
        return validate_repo_root(root.clone())
            .map_err(|err| format_repo_root_error(&root, err, true));
    }
    let guessed_root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .ok_or_else(|| "无法推断仓库根目录".to_string())?;
    validate_repo_root(guessed_root.clone())
        .map_err(|err| format_repo_root_error(&guessed_root, err, false))
}

fn discover_packaged_python_path(python_root: &Path) -> Vec<PathBuf> {
    let lib_root = python_root.join("lib");
    let Ok(entries) = std::fs::read_dir(lib_root) else {
        return Vec::new();
    };
    let mut paths = entries
        .flatten()
        .map(|entry| entry.path())
        .filter_map(|path| {
            let name = path.file_name()?.to_str()?;
            if !name.starts_with("python") {
                return None;
            }
            let site_packages = path.join("site-packages");
            if site_packages.is_dir() {
                Some(site_packages)
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    paths.sort();
    paths
}

fn runtime_metadata_matches(source_root: &Path, installed_root: &Path) -> bool {
    let source_metadata = source_root.join("runtime-metadata.json");
    let installed_metadata = installed_root.join("runtime-metadata.json");
    match (
        std::fs::read(source_metadata),
        std::fs::read(installed_metadata),
    ) {
        (Ok(left), Ok(right)) => left == right,
        _ => false,
    }
}

fn copy_directory_recursive(source: &Path, destination: &Path) -> Result<(), String> {
    std::fs::create_dir_all(destination)
        .map_err(|err| format!("创建运行时目录失败：{} ({err})", destination.display()))?;
    for entry in std::fs::read_dir(source)
        .map_err(|err| format!("读取运行时目录失败：{} ({err})", source.display()))?
    {
        let entry = entry.map_err(|err| format!("读取运行时条目失败：{err}"))?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        let file_type = entry
            .file_type()
            .map_err(|err| format!("读取运行时文件类型失败：{} ({err})", source_path.display()))?;
        if file_type.is_dir() {
            copy_directory_recursive(&source_path, &destination_path)?;
        } else {
            if let Some(parent) = destination_path.parent() {
                std::fs::create_dir_all(parent)
                    .map_err(|err| format!("创建运行时父目录失败：{} ({err})", parent.display()))?;
            }
            std::fs::copy(&source_path, &destination_path).map_err(|err| {
                format!(
                    "复制运行时文件失败：{} -> {} ({err})",
                    source_path.display(),
                    destination_path.display()
                )
            })?;
        }
    }
    Ok(())
}

fn snapshot_preserved_runtime_files(
    installed_root: &Path,
) -> Result<Vec<(PathBuf, Vec<u8>)>, String> {
    let mut preserved = Vec::new();
    for relative in PRESERVED_RUNTIME_FILES {
        let relative_path = PathBuf::from(relative);
        let source_path = installed_root.join(&relative_path);
        if !source_path.is_file() {
            continue;
        }
        let bytes = std::fs::read(&source_path).map_err(|err| {
            format!(
                "读取待保留运行时文件失败：{} ({err})",
                source_path.display()
            )
        })?;
        preserved.push((relative_path, bytes));
    }
    Ok(preserved)
}

fn restore_preserved_runtime_files(
    installed_root: &Path,
    preserved: &[(PathBuf, Vec<u8>)],
) -> Result<(), String> {
    for (relative_path, bytes) in preserved {
        let target_path = installed_root.join(relative_path);
        if let Some(parent) = target_path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|err| format!("创建保留文件父目录失败：{} ({err})", parent.display()))?;
        }
        std::fs::write(&target_path, bytes)
            .map_err(|err| format!("恢复保留运行时文件失败：{} ({err})", target_path.display()))?;
    }
    Ok(())
}

fn install_packaged_runtime(source_root: &Path, app_data_dir: PathBuf) -> Result<PathBuf, String> {
    let installed_root = app_data_dir.join(PACKAGED_RUNTIME_ROOT);
    let needs_refresh =
        !installed_root.exists() || !runtime_metadata_matches(source_root, &installed_root);
    if needs_refresh {
        let preserved = if installed_root.exists() {
            snapshot_preserved_runtime_files(&installed_root)?
        } else {
            Vec::new()
        };
        if installed_root.exists() {
            std::fs::remove_dir_all(&installed_root).map_err(|err| {
                format!("清理旧运行时目录失败：{} ({err})", installed_root.display())
            })?;
        }
        std::fs::create_dir_all(&app_data_dir)
            .map_err(|err| format!("创建 app data 目录失败：{} ({err})", app_data_dir.display()))?;
        copy_directory_recursive(source_root, &installed_root)?;
        restore_preserved_runtime_files(&installed_root, &preserved)?;
    }
    extract_packaged_node_archive(&installed_root)?;
    Ok(installed_root)
}

fn extract_packaged_node_archive(installed_root: &Path) -> Result<(), String> {
    let archive_path = installed_root.join(PACKAGED_NODE_ARCHIVE);
    let node_root = installed_root.join("node");
    if !archive_path.is_file() || node_root.exists() {
        return Ok(());
    }
    let tar_cmd = if cfg!(target_os = "windows") {
        "tar"
    } else {
        "/usr/bin/tar"
    };
    let status = Command::new(tar_cmd)
        .arg("-xzf")
        .arg(&archive_path)
        .arg("-C")
        .arg(installed_root)
        .status()
        .map_err(|err| {
            format!(
                "解压包内 Node 运行时失败：{} ({err})",
                archive_path.display()
            )
        })?;
    if !status.success() {
        return Err(format!(
            "解压包内 Node 运行时失败：{}，退出码 {:?}",
            archive_path.display(),
            status.code()
        ));
    }
    Ok(())
}

fn packaged_runtime_from(
    resource_dir: PathBuf,
    app_data_dir: Option<PathBuf>,
) -> Result<Option<RuntimeContext>, String> {
    let packaged_root = resource_dir.join(PACKAGED_RUNTIME_ROOT);
    if !packaged_root.exists() {
        return Ok(None);
    }
    let runtime_root = if let Some(app_data_dir) = app_data_dir {
        install_packaged_runtime(&packaged_root, app_data_dir)?
    } else {
        packaged_root
    };
    let repo_root = validate_repo_root(runtime_root.join(PACKAGED_REPO_ROOT))
        .map_err(|err| format!("包内运行时缺失引擎目录：{err}"))?;
    let python_home = runtime_root.join(PACKAGED_PYTHON_ROOT);
    let python_executable = runtime_root.join(PACKAGED_PYTHON_EXECUTABLE);
    let node_executable = runtime_root.join(PACKAGED_NODE_EXECUTABLE);
    if !python_executable.is_file() {
        return Err(format!(
            "包内运行时缺失 Python 可执行文件：{}",
            python_executable.display()
        ));
    }
    if !node_executable.is_file() {
        return Err(format!(
            "包内运行时缺失 Node 可执行文件：{}",
            node_executable.display()
        ));
    }
    Ok(Some(RuntimeContext {
        source: RuntimeSource::Packaged,
        repo_root,
        python_executable,
        python_home: Some(python_home.clone()),
        python_path: discover_packaged_python_path(&python_home),
        node_executable: Some(node_executable),
    }))
}

fn resolve_runtime_context_from(
    manifest_dir: PathBuf,
    explicit_root: Option<PathBuf>,
    explicit_python: Option<PathBuf>,
    resource_dir: Option<PathBuf>,
    app_data_dir: Option<PathBuf>,
) -> Result<RuntimeContext, String> {
    if let Some(resource_dir) = resource_dir {
        if let Some(context) = packaged_runtime_from(resource_dir, app_data_dir)? {
            return Ok(context);
        }
    }
    let has_explicit_override = explicit_root.is_some() || explicit_python.is_some();
    let repo_root = resolve_repo_root_from(manifest_dir, explicit_root)?;
    let python_executable = explicit_python.unwrap_or_else(|| PathBuf::from("python3"));
    Ok(RuntimeContext {
        source: if has_explicit_override {
            RuntimeSource::Explicit
        } else {
            RuntimeSource::Development
        },
        repo_root,
        python_executable,
        python_home: None,
        python_path: Vec::new(),
        node_executable: None,
    })
}

fn runtime_context(app: Option<&AppHandle>) -> Result<RuntimeContext, String> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let explicit_root = std::env::var("ORDO_REPO_ROOT").ok().map(PathBuf::from);
    let explicit_python = std::env::var("ORDO_PYTHON").ok().map(PathBuf::from);
    let resource_dir = app.and_then(|handle| handle.path().resource_dir().ok());
    let app_data_dir = app.and_then(|handle| handle.path().app_data_dir().ok());
    resolve_runtime_context_from(
        manifest_dir,
        explicit_root,
        explicit_python,
        resource_dir,
        app_data_dir,
    )
}

fn format_python_spawn_error(binary: &str, err: &std::io::Error) -> String {
    match err.kind() {
        std::io::ErrorKind::NotFound => format!(
            "启动 Python bridge 失败：未找到 Python 可执行文件 `{binary}`。请先安装 Python 3，或通过 ORDO_PYTHON 指定解释器路径。原始错误：{err}"
        ),
        _ => format!(
            "启动 Python bridge 失败：无法执行 `{binary}`。如有需要可通过 ORDO_PYTHON 指定解释器路径。原始错误：{err}"
        ),
    }
}

fn run_bridge_once(app: Option<&AppHandle>, payload: Value) -> Result<Value, String> {
    let runtime = runtime_context(app)?;
    let root = runtime.repo_root.clone();
    let script = runtime.bridge_script_path();
    let request = serde_json::to_vec(&payload).map_err(|err| err.to_string())?;
    let python = runtime.python_display();
    let mut command = Command::new(&python);
    command
        .arg(script)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    runtime.apply_runtime_env(&mut command)?;
    let mut child = command
        .spawn()
        .map_err(|err| format_python_spawn_error(&python, &err))?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| "无法打开 Python bridge stdin".to_string())?
        .write_all(&request)
        .map_err(|err| format!("写入 Python bridge 请求失败: {err}"))?;
    let output = child
        .wait_with_output()
        .map_err(|err| format!("等待 Python bridge 结果失败: {err}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("Python bridge 失败，退出码 {:?}", output.status.code())
        } else {
            stderr
        });
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|err| format!("解析 Python bridge JSON 失败: {err}"))
}

#[tauri::command]
fn bridge_request(app: AppHandle, payload: Value) -> Result<Value, String> {
    run_bridge_once(Some(&app), payload)
}

#[tauri::command]
fn run_publish_job_stream(app: AppHandle, window: Window, plan: Value) -> Result<Value, String> {
    let runtime = runtime_context(Some(&app))?;
    let root = runtime.repo_root.clone();
    let script = runtime.bridge_script_path();
    let request = serde_json::to_vec(&json!({
        "command": "run_publish_job_stream",
        "plan": plan,
    }))
    .map_err(|err| err.to_string())?;
    let python = runtime.python_display();
    let mut command = Command::new(&python);
    command
        .arg(script)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    runtime.apply_runtime_env(&mut command)?;
    let mut child = command.spawn().map_err(|err| {
        format!(
            "启动发布任务失败：{}",
            format_python_spawn_error(&python, &err)
        )
    })?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| "无法打开发布任务 stdin".to_string())?
        .write_all(&request)
        .map_err(|err| format!("写入发布任务请求失败: {err}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "无法读取发布任务 stdout".to_string())?;
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    let mut final_payload: Option<Value> = None;

    loop {
        line.clear();
        let bytes = reader
            .read_line(&mut line)
            .map_err(|err| format!("读取发布事件失败: {err}"))?;
        if bytes == 0 {
            break;
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let event: Value =
            serde_json::from_str(trimmed).map_err(|err| format!("解析发布事件失败: {err}"))?;
        if event.get("type").and_then(Value::as_str) == Some("command_result") {
            final_payload = event.get("payload").cloned();
        } else {
            window
                .emit("publish-event", &event)
                .map_err(|err| format!("发送桌面发布事件失败: {err}"))?;
        }
    }

    let output = child
        .wait_with_output()
        .map_err(|err| format!("等待发布任务退出失败: {err}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("发布任务失败，退出码 {:?}", output.status.code())
        } else {
            stderr
        });
    }

    final_payload.ok_or_else(|| "发布任务未返回最终结果".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_request,
            run_publish_job_stream
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{
        format_python_spawn_error, install_packaged_runtime, resolve_repo_root_from,
        resolve_runtime_context_from, validate_repo_root, RuntimeSource,
    };
    use std::fs;
    use std::io;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_path(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos();
        std::env::temp_dir().join(format!("ordo-desktop-{name}-{nonce}"))
    }

    #[test]
    fn validate_repo_root_accepts_directory_with_bridge_script() {
        let root = temp_path("valid-root");
        fs::create_dir_all(root.join("scripts")).expect("create scripts dir");
        fs::write(
            root.join("scripts").join("workbench_bridge.py"),
            "print('ok')",
        )
        .expect("write bridge");

        let validated = validate_repo_root(root.clone()).expect("valid repo root");

        assert_eq!(validated, root);
    }

    #[test]
    fn resolve_repo_root_prefers_explicit_override() {
        let override_root = temp_path("override-root");
        fs::create_dir_all(override_root.join("scripts")).expect("create scripts dir");
        fs::write(
            override_root.join("scripts").join("workbench_bridge.py"),
            "print('ok')",
        )
        .expect("write bridge");

        let resolved = resolve_repo_root_from(
            PathBuf::from("/tmp/unused/desktop/src-tauri"),
            Some(override_root.clone()),
        )
        .expect("resolve repo root");

        assert_eq!(resolved, override_root);
    }

    #[test]
    fn resolve_repo_root_error_mentions_ordo_repo_root_override() {
        let err = resolve_repo_root_from(
            PathBuf::from("/tmp/unused/desktop/src-tauri"),
            Some(PathBuf::from("/tmp/missing-ordo-root")),
        )
        .expect_err("missing override root should fail");

        assert!(err.contains("ORDO_REPO_ROOT"), "unexpected error: {err}");
    }

    #[test]
    fn resolve_runtime_context_prefers_packaged_runtime_when_present() {
        let resource_root = temp_path("packaged-runtime");
        let app_data_root = temp_path("app-data-runtime");
        let packaged_root = resource_root.join("ordo-runtime");
        let installed_root = app_data_root.join("ordo-runtime");
        let packaged_repo_root = packaged_root.join("repo");
        let installed_repo_root = installed_root.join("repo");
        let packaged_python_root = packaged_root.join("python");
        let installed_python_root = installed_root.join("python");
        let packaged_python_executable = packaged_python_root.join("bin").join("python3");
        let installed_python_executable = installed_python_root.join("bin").join("python3");
        let packaged_node_root = packaged_root.join("node");
        let installed_node_root = installed_root.join("node");
        let packaged_node_executable = packaged_node_root.join("bin").join("node");
        let installed_node_executable = installed_node_root.join("bin").join("node");
        let installed_site_packages = installed_python_root
            .join("lib")
            .join("python3.11")
            .join("site-packages");

        fs::create_dir_all(packaged_repo_root.join("scripts"))
            .expect("create packaged scripts dir");
        fs::create_dir_all(
            packaged_python_root
                .join("lib")
                .join("python3.11")
                .join("site-packages"),
        )
        .expect("create site packages dir");
        fs::create_dir_all(packaged_python_executable.parent().expect("python parent"))
            .expect("create python bin dir");
        fs::create_dir_all(packaged_node_executable.parent().expect("node parent"))
            .expect("create node bin dir");
        fs::write(
            packaged_repo_root
                .join("scripts")
                .join("workbench_bridge.py"),
            "print('ok')",
        )
        .expect("write packaged bridge");
        fs::write(&packaged_python_executable, "").expect("write packaged python");
        fs::write(&packaged_node_executable, "").expect("write packaged node");
        fs::write(
            packaged_root.join("runtime-metadata.json"),
            "{\"source_fingerprint\":\"one\"}",
        )
        .expect("write metadata");

        let explicit_root = temp_path("explicit-root");
        fs::create_dir_all(explicit_root.join("scripts")).expect("create explicit scripts dir");
        fs::write(
            explicit_root.join("scripts").join("workbench_bridge.py"),
            "print('ok')",
        )
        .expect("write explicit bridge");

        let context = resolve_runtime_context_from(
            PathBuf::from("/tmp/unused/desktop/src-tauri"),
            Some(explicit_root),
            Some(PathBuf::from("/tmp/system/python3")),
            Some(resource_root),
            Some(app_data_root),
        )
        .expect("resolve packaged runtime");

        assert_eq!(context.source, RuntimeSource::Packaged);
        assert_eq!(context.repo_root, installed_repo_root);
        assert_eq!(context.python_executable, installed_python_executable);
        assert_eq!(context.python_home, Some(installed_python_root));
        assert_eq!(context.node_executable, Some(installed_node_executable));
        assert_eq!(context.python_path, vec![installed_site_packages]);
    }

    #[test]
    fn install_packaged_runtime_preserves_saved_repo_settings_on_refresh() {
        let source_root = temp_path("packaged-source-root");
        let app_data_root = temp_path("packaged-app-data-root");
        let installed_root = app_data_root.join("ordo-runtime");

        fs::create_dir_all(source_root.join("repo").join("scripts"))
            .expect("create packaged repo scripts dir");
        fs::write(
            source_root
                .join("repo")
                .join("scripts")
                .join("workbench_bridge.py"),
            "print('ok')",
        )
        .expect("write packaged bridge");
        fs::write(
            source_root.join("repo").join("publish.py"),
            "print('publish')",
        )
        .expect("write packaged publish");
        fs::write(
            source_root.join("runtime-metadata.json"),
            "{\"source_fingerprint\":\"new\"}",
        )
        .expect("write source metadata");

        fs::create_dir_all(installed_root.join("repo").join("scripts"))
            .expect("create installed repo scripts dir");
        fs::write(
            installed_root
                .join("repo")
                .join("scripts")
                .join("workbench_bridge.py"),
            "print('old')",
        )
        .expect("write installed bridge");
        fs::write(
            installed_root.join("repo").join("secrets.env"),
            "WECHAT_APPID=saved\nWECHAT_SECRET=saved-secret\n",
        )
        .expect("write installed secrets");
        fs::write(
            installed_root.join("runtime-metadata.json"),
            "{\"source_fingerprint\":\"old\"}",
        )
        .expect("write installed metadata");

        let refreshed_root = install_packaged_runtime(&source_root, app_data_root.clone())
            .expect("refresh packaged runtime");

        assert_eq!(refreshed_root, installed_root);
        assert_eq!(
            fs::read_to_string(installed_root.join("repo").join("secrets.env"))
                .expect("read preserved secrets"),
            "WECHAT_APPID=saved\nWECHAT_SECRET=saved-secret\n"
        );
        assert_eq!(
            fs::read_to_string(installed_root.join("repo").join("publish.py"))
                .expect("read refreshed publish"),
            "print('publish')"
        );
    }

    #[test]
    fn resolve_runtime_context_falls_back_to_explicit_dev_paths() {
        let explicit_root = temp_path("explicit-runtime-root");
        let explicit_python = PathBuf::from("/tmp/custom-python");
        fs::create_dir_all(explicit_root.join("scripts")).expect("create explicit scripts dir");
        fs::write(
            explicit_root.join("scripts").join("workbench_bridge.py"),
            "print('ok')",
        )
        .expect("write explicit bridge");

        let context = resolve_runtime_context_from(
            PathBuf::from("/tmp/unused/desktop/src-tauri"),
            Some(explicit_root.clone()),
            Some(explicit_python.clone()),
            Some(temp_path("missing-packaged-runtime")),
            Some(temp_path("missing-app-data-runtime")),
        )
        .expect("resolve explicit runtime");

        assert_eq!(context.source, RuntimeSource::Explicit);
        assert_eq!(context.repo_root, explicit_root);
        assert_eq!(context.python_executable, explicit_python);
        assert_eq!(context.python_home, None);
        assert_eq!(context.node_executable, None);
        assert!(context.python_path.is_empty());
    }

    #[test]
    fn format_python_spawn_error_mentions_ordo_python_when_binary_missing() {
        let err = format_python_spawn_error(
            "missing-python",
            &io::Error::new(io::ErrorKind::NotFound, "missing-python"),
        );

        assert!(err.contains("ORDO_PYTHON"), "unexpected error: {err}");
    }
}
