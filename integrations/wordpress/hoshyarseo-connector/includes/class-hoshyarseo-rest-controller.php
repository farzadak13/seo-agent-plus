<?php
/**
 * REST endpoints used by the HoshyarSEO service.
 *
 *   GET  /wp-json/hoshyarseo/v1/status
 *   GET  /wp-json/hoshyarseo/v1/page?url=...
 *   POST /wp-json/hoshyarseo/v1/title     {url, title, idempotency_key}
 *   POST /wp-json/hoshyarseo/v1/rollback  {url, change_id}
 *
 * Authentication is WordPress's own: an application password for a user who
 * can edit the page. The plugin adds no credentials of its own, so revoking
 * that application password disconnects HoshyarSEO completely.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * REST controller.
 */
class HoshyarSEO_REST_Controller {

	const REST_NAMESPACE = 'hoshyarseo/v1';

	/**
	 * Override storage.
	 *
	 * @var HoshyarSEO_Title_Store
	 */
	private $store;

	/**
	 * Cache purger.
	 *
	 * @var HoshyarSEO_Cache
	 */
	private $cache;

	/**
	 * Constructor.
	 *
	 * @param HoshyarSEO_Title_Store $store Override storage.
	 * @param HoshyarSEO_Cache       $cache Cache purger.
	 */
	public function __construct( HoshyarSEO_Title_Store $store, HoshyarSEO_Cache $cache ) {
		$this->store = $store;
		$this->cache = $cache;
	}

	/**
	 * Register routes.
	 *
	 * @return void
	 */
	public function register_routes() {
		$url_arg = array(
			'required'          => true,
			'type'              => 'string',
			'sanitize_callback' => 'esc_url_raw',
		);

		register_rest_route(
			self::REST_NAMESPACE,
			'/status',
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => array( $this, 'status' ),
				'permission_callback' => array( $this, 'can_edit_posts' ),
			)
		);

		register_rest_route(
			self::REST_NAMESPACE,
			'/page',
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => array( $this, 'page' ),
				'permission_callback' => array( $this, 'can_edit_page' ),
				'args'                => array( 'url' => $url_arg ),
			)
		);

		register_rest_route(
			self::REST_NAMESPACE,
			'/title',
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => array( $this, 'set_title' ),
				'permission_callback' => array( $this, 'can_edit_page' ),
				'args'                => array(
					'url'             => $url_arg,
					'title'           => array(
						'required' => true,
						'type'     => 'string',
					),
					'idempotency_key' => array(
						'required'          => true,
						'type'              => 'string',
						'sanitize_callback' => 'sanitize_text_field',
					),
				),
			)
		);

		register_rest_route(
			self::REST_NAMESPACE,
			'/rollback',
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => array( $this, 'rollback' ),
				'permission_callback' => array( $this, 'can_edit_page' ),
				'args'                => array(
					'url'       => $url_arg,
					'change_id' => array(
						'required'          => true,
						'type'              => 'string',
						'sanitize_callback' => 'sanitize_text_field',
					),
				),
			)
		);
	}

	/**
	 * Permission: any user who can edit posts. Used for the connection check.
	 *
	 * @return bool|WP_Error
	 */
	public function can_edit_posts() {
		if ( ! is_user_logged_in() ) {
			return new WP_Error( 'rest_not_logged_in', __( 'Authentication required.', 'hoshyarseo-connector' ), array( 'status' => 401 ) );
		}
		return current_user_can( 'edit_posts' );
	}

	/**
	 * Permission: the user can edit the specific page named by ?url=.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return bool|WP_Error
	 */
	public function can_edit_page( WP_REST_Request $request ) {
		$allowed = $this->can_edit_posts();
		if ( true !== $allowed ) {
			return $allowed;
		}
		$post_id = $this->resolve( $request->get_param( 'url' ) );
		if ( is_wp_error( $post_id ) ) {
			return $post_id;
		}
		return current_user_can( 'edit_post', $post_id );
	}

	/**
	 * Plugin version and the SEO plugins it found, for the connection check.
	 *
	 * @return WP_REST_Response
	 */
	public function status() {
		return rest_ensure_response(
			array(
				'plugin_version' => HOSHYARSEO_CONNECTOR_VERSION,
				'seo_plugins'    => $this->detect_seo_plugins(),
				'home_url'       => home_url( '/' ),
			)
		);
	}

	/**
	 * What the service needs to know about one page.
	 *
	 * The rendered <title> is not computed here: the service reads it from the
	 * public page, which is the only place it is certain to be what visitors
	 * and Google see.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response|WP_Error
	 */
	public function page( WP_REST_Request $request ) {
		$post_id = $this->resolve( $request->get_param( 'url' ) );
		if ( is_wp_error( $post_id ) ) {
			return $post_id;
		}
		$post = get_post( $post_id );

		return rest_ensure_response(
			array(
				'post_id'        => $post_id,
				'post_type'      => $post->post_type,
				'status'         => $post->post_status,
				'permalink'      => get_permalink( $post_id ),
				'post_title'     => $post->post_title,
				'override_title' => $this->store->get( $post_id ),
				'seo_plugins'    => $this->detect_seo_plugins(),
				'modified_gmt'   => $post->post_modified_gmt,
			)
		);
	}

	/**
	 * Apply a title override.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response|WP_Error
	 */
	public function set_title( WP_REST_Request $request ) {
		$post_id = $this->resolve( $request->get_param( 'url' ) );
		if ( is_wp_error( $post_id ) ) {
			return $post_id;
		}
		$change = $this->store->apply(
			$post_id,
			(string) $request->get_param( 'title' ),
			(string) $request->get_param( 'idempotency_key' )
		);
		if ( is_wp_error( $change ) ) {
			return $change;
		}
		$change['purged']  = $this->cache->purge( $post_id );
		$change['post_id'] = $post_id;
		return rest_ensure_response( $change );
	}

	/**
	 * Undo one change.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return WP_REST_Response|WP_Error
	 */
	public function rollback( WP_REST_Request $request ) {
		$post_id = $this->resolve( $request->get_param( 'url' ) );
		if ( is_wp_error( $post_id ) ) {
			return $post_id;
		}
		$change = $this->store->rollback( $post_id, (string) $request->get_param( 'change_id' ) );
		if ( is_wp_error( $change ) ) {
			return $change;
		}
		$change['purged']  = $this->cache->purge( $post_id );
		$change['post_id'] = $post_id;
		return rest_ensure_response( $change );
	}

	/**
	 * Map a public URL on this site to a post ID.
	 *
	 * @param string $url Public URL.
	 * @return int|WP_Error
	 */
	private function resolve( $url ) {
		$url       = esc_url_raw( (string) $url );
		$url_host  = wp_parse_url( $url, PHP_URL_HOST );
		$site_host = wp_parse_url( home_url(), PHP_URL_HOST );

		if ( ! $url_host || strtolower( $url_host ) !== strtolower( (string) $site_host ) ) {
			return new WP_Error( 'hoshyarseo_foreign_url', __( 'The URL does not belong to this site.', 'hoshyarseo-connector' ), array( 'status' => 422 ) );
		}

		$post_id = url_to_postid( $url );
		if ( ! $post_id ) {
			return new WP_Error( 'hoshyarseo_not_found', __( 'No post or page was found at this URL.', 'hoshyarseo-connector' ), array( 'status' => 404 ) );
		}
		return absint( $post_id );
	}

	/**
	 * Which SEO plugins are active.
	 *
	 * @return string[]
	 */
	private function detect_seo_plugins() {
		$found = array();
		if ( defined( 'WPSEO_VERSION' ) ) {
			$found[] = 'yoast';
		}
		if ( class_exists( 'RankMath' ) ) {
			$found[] = 'rank-math';
		}
		if ( defined( 'AIOSEO_VERSION' ) ) {
			$found[] = 'aioseo';
		}
		if ( defined( 'SEOPRESS_VERSION' ) ) {
			$found[] = 'seopress';
		}
		return $found;
	}
}
