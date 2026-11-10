import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils, type VRM } from '@pixiv/three-vrm'

const maximumPixelRatio = 2

export function isVrmFile(file: Pick<File, 'name'>): boolean {
  return file.name.toLowerCase().endsWith('.vrm')
}

export function boundAvatarEnergy(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
}

/** Render one local VRM or a neutral fallback without owning browser audio playback. */
export class BrowserAvatar {
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100)
  private readonly timer = new THREE.Timer()
  private readonly fallback = new THREE.Group()
  private readonly fallbackMouth: THREE.Mesh
  private host: HTMLElement | null = null
  private renderer: THREE.WebGLRenderer | null = null
  private resizeObserver: ResizeObserver | null = null
  private frameId: number | null = null
  private vrm: VRM | null = null
  private energy = 0

  constructor() {
    this.scene.background = new THREE.Color('#edf5f2')
    this.camera.position.set(0, 0.4, 3.4)
    this.scene.add(new THREE.HemisphereLight('#f7fff9', '#183a33', 2.5))
    const keyLight = new THREE.DirectionalLight('#fff0d4', 2.8)
    keyLight.position.set(2.5, 3, 3)
    this.scene.add(keyLight)
    this.fallbackMouth = this.addFallbackAvatar()
    this.scene.add(this.fallback)
  }

  mount(host: HTMLElement): void {
    this.disposeRenderer()
    this.host = host
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maximumPixelRatio))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.domElement.className = 'avatar-canvas'
    host.replaceChildren(renderer.domElement)
    this.renderer = renderer
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(host)
    this.resize()
    this.render(performance.now())
  }

  async load(file: File): Promise<void> {
    if (!isVrmFile(file)) throw new Error('请选择 .vrm 文件。')
    const loader = new GLTFLoader()
    loader.register((parser) => new VRMLoaderPlugin(parser))
    const gltf = await loader.parseAsync(await file.arrayBuffer(), '')
    const vrm = gltf.userData.vrm
    if (!vrm) throw new Error('该文件不是可加载的 VRM 模型。')
    this.unloadVrm()
    VRMUtils.rotateVRM0(vrm)
    this.frameVrm(vrm)
    this.scene.add(vrm.scene)
    this.vrm = vrm
    this.fallback.visible = false
  }

  setEnergy(value: number): void {
    this.energy = boundAvatarEnergy(value)
  }

  dispose(): void {
    this.unloadVrm()
    this.disposeRenderer()
  }

  private addFallbackAvatar(): THREE.Mesh {
    const skin = new THREE.MeshStandardMaterial({ color: '#c28c73', roughness: 0.82 })
    const dark = new THREE.MeshStandardMaterial({ color: '#1d2b32', roughness: 0.55 })
    const sweater = new THREE.MeshStandardMaterial({ color: '#17614b', roughness: 0.78 })
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.78, 40, 32), skin)
    head.position.y = 0.42
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.58, 1.2, 8, 24), sweater)
    body.position.y = -1.02
    const leftEye = new THREE.Mesh(new THREE.SphereGeometry(0.09, 18, 14), dark)
    leftEye.position.set(-0.27, 0.58, 0.7)
    const rightEye = leftEye.clone()
    rightEye.position.x = 0.27
    const mouth = new THREE.Mesh(new THREE.SphereGeometry(0.2, 22, 16), dark)
    mouth.position.set(0, 0.08, 0.73)
    mouth.scale.set(1.2, 0.22, 0.22)
    this.fallback.add(head, body, leftEye, rightEye, mouth)
    return mouth
  }

  private render = (timestamp: number): void => {
    const renderer = this.renderer
    if (!renderer) return
    this.timer.update(timestamp)
    const delta = this.timer.getDelta()
    const mouthOpen = 0.22 + this.energy * 1.55
    this.fallbackMouth.scale.y = mouthOpen
    this.vrm?.expressionManager?.setValue('aa', this.energy)
    this.vrm?.update(delta)
    renderer.render(this.scene, this.camera)
    this.frameId = requestAnimationFrame(this.render)
  }

  private resize(): void {
    if (!this.host || !this.renderer) return
    const { width, height } = this.host.getBoundingClientRect()
    if (width < 1 || height < 1) return
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height, false)
  }

  private unloadVrm(): void {
    if (!this.vrm) return
    this.scene.remove(this.vrm.scene)
    VRMUtils.deepDispose(this.vrm.scene)
    this.vrm = null
    this.fallback.visible = true
  }

  private frameVrm(vrm: VRM): void {
    const bounds = new THREE.Box3().setFromObject(vrm.scene)
    const size = bounds.getSize(new THREE.Vector3())
    if (size.y <= 0) return
    const targetHeight = 2.5
    vrm.scene.scale.setScalar(targetHeight / size.y)
    bounds.setFromObject(vrm.scene)
    const center = bounds.getCenter(new THREE.Vector3())
    vrm.scene.position.x -= center.x
    vrm.scene.position.z -= center.z
    vrm.scene.position.y += -1.62 - bounds.min.y
  }

  private disposeRenderer(): void {
    if (this.frameId !== null) cancelAnimationFrame(this.frameId)
    this.frameId = null
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    this.renderer?.dispose()
    this.timer.dispose()
    this.renderer?.domElement.remove()
    this.renderer = null
    this.host = null
  }
}
