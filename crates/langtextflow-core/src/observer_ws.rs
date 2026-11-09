//! Read-only, loopback-only observer. Rust owns projection and authorized snapshots.
use crate::asr_slice::AsrSession;
use crate::caption_events::{MAX_OBSERVERS, MAX_SNAPSHOT_BYTES};
use futures_util::{
    future::{select, Either},
    SinkExt, StreamExt,
};
use std::net::{Ipv4Addr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{watch, Semaphore};
use tokio::task::{JoinHandle, JoinSet};
use tokio::time::timeout;
use tokio_tungstenite::{
    accept_hdr_async_with_config,
    tungstenite::{
        handshake::server::{ErrorResponse, Request, Response},
        http::{StatusCode, Uri},
        protocol::{frame::coding::CloseCode, CloseFrame, WebSocketConfig},
        Message,
    },
};

const HANDSHAKE_BYTES: usize = 16 * 1024;
const HANDSHAKE_BUDGET: Duration = Duration::from_secs(2);
const SEND_BUDGET: Duration = Duration::from_millis(250);

pub struct ObserverServer {
    address: SocketAddr,
    shutdown: Option<watch::Sender<bool>>,
    task: Option<JoinHandle<()>>,
}

fn local_origin(origin: &str) -> bool {
    origin.parse::<Uri>().ok().is_some_and(|uri| {
        matches!(uri.scheme_str(), Some("http" | "https"))
            && matches!(
                uri.host(),
                Some("localhost" | "127.0.0.1" | "[::1]" | "::1")
            )
            && uri
                .authority()
                .is_some_and(|authority| !authority.as_str().contains('@'))
            && uri.path() == "/"
            && uri.query().is_none()
    })
}

async fn bounded_headers(stream: &TcpStream) -> Result<(), &'static str> {
    let mut header = [0_u8; HANDSHAKE_BYTES];
    loop {
        let size = stream.peek(&mut header).await.map_err(|_| "handshake_io")?;
        if size == 0 {
            return Err("handshake_closed");
        }
        if header[..size].windows(4).any(|part| part == b"\r\n\r\n") {
            return Ok(());
        }
        if size == HANDSHAKE_BYTES {
            return Err("handshake_limit");
        }
        // Peek preserves bytes for the maintained HTTP/WS parser. Avoid spinning
        // on an unchanged partial header from an unauthenticated client.
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

async fn metric_frame(
    receiver: &mut Option<watch::Receiver<Option<crate::caption_events::JsonFrame>>>,
) -> crate::caption_events::JsonFrame {
    if let Some(receiver) = receiver {
        loop {
            if receiver.changed().await.is_err() {
                break;
            }
            if let Some(frame) = receiver.borrow_and_update().clone() {
                return frame;
            }
        }
    }
    std::future::pending().await
}

fn deny() -> ErrorResponse {
    Response::builder()
        .status(StatusCode::FORBIDDEN)
        .body(Some("observer authorization required".into()))
        .unwrap()
}

async fn connection(
    stream: TcpStream,
    session: AsrSession,
    origin: Arc<str>,
    mut server_stop: watch::Receiver<bool>,
) {
    let authorize = session.clone();
    let mut token = String::new();
    let callback = |request: &Request, response: Response| -> Result<Response, ErrorResponse> {
        let path = request
            .uri()
            .path_and_query()
            .map(|value| value.as_str())
            .unwrap_or("");
        let candidate = path.strip_prefix("/ws/observer?token=").unwrap_or("");
        if candidate.len() != 64
            || !candidate.bytes().all(|byte| byte.is_ascii_hexdigit())
            || request
                .headers()
                .get("origin")
                .and_then(|value| value.to_str().ok())
                != Some(origin.as_ref())
            || !authorize.authorize_observer(candidate)
        {
            return Err(deny());
        }
        token = candidate.into();
        Ok(response)
    };
    let config = WebSocketConfig::default()
        .read_buffer_size(4096)
        .write_buffer_size(0)
        .max_write_buffer_size(MAX_SNAPSHOT_BYTES + 4096)
        .max_message_size(Some(4096))
        .max_frame_size(Some(4096));
    let handshake = async {
        bounded_headers(&stream).await?;
        accept_hdr_async_with_config(stream, callback, Some(config))
            .await
            .map_err(|_| "handshake_failed")
    };
    let mut ws = match timeout(HANDSHAKE_BUDGET, handshake).await {
        Ok(Ok(socket)) => socket,
        _ => return,
    };
    // Recheck under the authority lock *after* accept; a session can change in
    // the handshake. Snapshot enqueue and registration are one atomic operation.
    let mut attachment = match session.subscribe_observer(&token) {
        Ok(attachment) => attachment,
        Err(_) => {
            let _ = timeout(SEND_BUDGET, ws.close(None)).await;
            return;
        }
    };
    enum Action {
        Frame(crate::caption_events::JsonFrame),
        Flush,
        Closed,
        PeerClose,
        PolicyClose,
        Reset,
        ServerStop,
    }
    let mut close_code = CloseCode::Restart;
    loop {
        // A ready control-frame flood must not monopolize a single-thread core.
        tokio::task::yield_now().await;
        if *server_stop.borrow() {
            close_code = CloseCode::Away;
            break;
        }
        if *attachment.receiver.control.borrow() {
            break;
        }
        let action = {
            let receive = Box::pin(ws.next());
            let frame = Box::pin(attachment.receiver.frames.recv());
            let control = Box::pin(attachment.receiver.control.changed());
            let stopped = Box::pin(server_stop.changed());
            let metrics = Box::pin(metric_frame(&mut attachment.receiver.telemetry));
            let observed = select(
                Box::pin(select(receive, frame)),
                Box::pin(select(control, stopped)),
            );
            match select(Box::pin(observed), metrics).await {
                Either::Right((frame, _)) => Action::Frame(frame),
                Either::Left((action, _)) => match action {
                    Either::Left((Either::Left((Some(Ok(Message::Ping(_))), _)), _)) => {
                        Action::Flush
                    }
                    Either::Left((Either::Left((Some(Ok(Message::Pong(_))), _)), _)) => continue,
                    Either::Left((Either::Left((Some(Ok(Message::Close(_))), _)), _)) => {
                        Action::PeerClose
                    }
                    Either::Left((Either::Left((Some(Ok(_)), _)), _)) => Action::PolicyClose,
                    Either::Left((Either::Left(_), _)) => Action::Closed,
                    Either::Left((Either::Right((Some(frame), _)), _)) => Action::Frame(frame),
                    Either::Left((Either::Right((None, _)), _)) => Action::Closed,
                    Either::Right((Either::Left(_), _)) => Action::Reset,
                    Either::Right((Either::Right(_), _)) => Action::ServerStop,
                },
            }
        };
        if *server_stop.borrow() {
            close_code = CloseCode::Away;
            break;
        }
        if *attachment.receiver.control.borrow() {
            break;
        }
        match action {
            Action::Frame(frame) => {
                match timeout(
                    SEND_BUDGET,
                    ws.send(Message::Text(frame.get().to_owned().into())),
                )
                .await
                {
                    Ok(Ok(())) => {}
                    Err(_) => {
                        session.observer_send_timeout(attachment.receiver.id);
                        return;
                    }
                    _ => return,
                }
            }
            Action::Flush => {
                if !matches!(timeout(SEND_BUDGET, ws.flush()).await, Ok(Ok(()))) {
                    return;
                }
            }
            Action::Closed => return,
            Action::PeerClose => {
                let _ = timeout(SEND_BUDGET, ws.flush()).await;
                return;
            }
            Action::PolicyClose => {
                close_code = CloseCode::Policy;
                break;
            }
            Action::Reset => break,
            Action::ServerStop => {
                close_code = CloseCode::Away;
                break;
            }
        }
    }
    let _ = timeout(
        SEND_BUDGET,
        ws.close(Some(CloseFrame {
            code: close_code,
            reason: "observer closed".into(),
        })),
    )
    .await;
}

impl ObserverServer {
    pub async fn bind(
        session: AsrSession,
        port: u16,
        allowed_origin: &str,
    ) -> Result<Self, &'static str> {
        if !local_origin(allowed_origin) {
            return Err("local_origin_required");
        }
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, port))
            .await
            .map_err(|_| "observer_bind")?;
        let address = listener.local_addr().map_err(|_| "observer_address")?;
        let (shutdown, mut stop) = watch::channel(false);
        let owned = session.clone();
        let origin: Arc<str> = Arc::from(allowed_origin);
        let task = tokio::spawn(async move {
            let capacity = Arc::new(Semaphore::new(MAX_OBSERVERS));
            let mut connections = JoinSet::new();
            loop {
                tokio::task::yield_now().await;
                if *stop.borrow() {
                    break;
                }
                while connections.try_join_next().is_some() {}
                let accepted =
                    match select(Box::pin(listener.accept()), Box::pin(stop.changed())).await {
                        Either::Left((Ok((stream, _)), _)) => stream,
                        _ => break,
                    };
                let Ok(permit) = capacity.clone().try_acquire_owned() else {
                    owned.observer_capacity_rejected();
                    drop(accepted);
                    continue;
                };
                let session = owned.clone();
                let origin = origin.clone();
                let control = stop.clone();
                connections.spawn(async move {
                    let _permit = permit;
                    connection(accepted, session, origin, control).await;
                });
            }
            drop(listener);
            if timeout(Duration::from_millis(500), async {
                while connections.join_next().await.is_some() {}
            })
            .await
            .is_err()
            {
                connections.abort_all();
                while connections.join_next().await.is_some() {}
            }
        });
        Ok(Self {
            address,
            shutdown: Some(shutdown),
            task: Some(task),
        })
    }

    pub fn address(&self) -> SocketAddr {
        self.address
    }

    pub async fn stop(mut self) -> Result<(), &'static str> {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(true);
        }
        if let Some(task) = self.task.as_mut() {
            match timeout(Duration::from_secs(1), task).await {
                Ok(Ok(())) => {}
                Ok(Err(_)) => return Err("observer_task_failed"),
                Err(_) => return Err("observer_stop_timeout"),
            }
        }
        self.task.take();
        Ok(())
    }
}

impl Drop for ObserverServer {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(true);
        }
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}
